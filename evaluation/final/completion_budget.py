"""Completion-only approved multi-experiment bounded text client and non-refundable durable cost ledger.

No prompts, source passages, response content, credentials, or HTTP error bodies
are stored in this ledger. Dispatch is bounded by conservative token allowances
and provider price filters; the local USD allowance is not a billing guarantee.
"""
import asyncio
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import hashlib
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat
import time

from workflow_contracts import WorkflowError, canonical, digest

TASK_MODEL = "openai/gpt-4.1-mini"
JUDGE_MODEL = "meta-llama/llama-3.3-70b-instruct"
APPROVED_MODELS = (TASK_MODEL, JUDGE_MODEL)
MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"
MAX_CALLS = 3000
MAX_CALLS_PER_CASE = 50
MAX_RESERVED_NANO_USD = 10_000_000_000
OUTPUT_TOKENS = 2200
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MAX_RESPONSE_BYTES = 512000
TIMEOUT_SECONDS = 60
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _fail(code, message):
    return WorkflowError(code, message)


def _valid_id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _critic_response_format(config):
    """Accept the exact production critic schema attested by the assay engine.

    The attestation is supplied by trusted local code, never source text or a
    model response. The complete format is snapshotted before reservation so
    callers cannot change its nested objects during the asynchronous request.
    """
    value = config.get("_response_format")
    expected = config.get("_assay_response_format_sha256")
    try:
        if (not isinstance(expected, str) or not _HASH.fullmatch(expected)
                or not isinstance(value, dict) or set(value) != {"type", "json_schema"}
                or value["type"] != "json_schema"):
            raise ValueError("Missing critic format attestation")
        spec = value["json_schema"]
        if (not isinstance(spec, dict) or set(spec) != {"name", "strict", "schema"}
                or spec["name"] != "critic_action" or spec["strict"] is not True):
            raise ValueError("Unexpected critic format")
        schema = spec["schema"]
        if (not isinstance(schema, dict) or schema.get("type") != "object"
                or not isinstance(schema.get("properties"), dict)
                or set(schema["properties"]) != {"tool", "arguments", "final"}):
            raise ValueError("Invalid critic action schema")

        def check_closed_objects(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    properties, required = node.get("properties"), node.get("required")
                    if (not isinstance(properties, dict) or node.get("additionalProperties") is not False
                            or not isinstance(required, list) or any(not isinstance(k, str) for k in required)
                            or len(required) != len(set(required)) or set(required) != set(properties)):
                        raise ValueError("Critic objects must have closed required properties")
                if "$ref" in node and (not isinstance(node["$ref"], str) or not node["$ref"].startswith("#/$defs/")):
                    raise ValueError("Only local critic definitions are permitted")
                for item in node.values():
                    check_closed_objects(item)
            elif isinstance(node, list):
                for item in node:
                    check_closed_objects(item)

        check_closed_objects(schema)
        if digest(value) != expected:
            raise ValueError("Critic format changed")
        return json.loads(canonical(value))
    except (ValueError, TypeError, RecursionError):
        raise _fail("live_config_mismatch", "The exact attested production critic schema is required. No model call was made.") from None


DEFAULT_LIMITS = {"max_calls": 3000, "max_calls_per_case": 50, "max_reserved_usd": "10.00",
                  "max_output_tokens": 2200, "max_input_bytes": 100000, "max_runtime_seconds": 10800}


def _decimal(value):
    if type(value) not in (str, int, float):
        raise ValueError("Invalid price")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or result > 100000:
        raise ValueError("Invalid price")
    return result


def _validate_limits(limits, *, allow_amended=False):
    if not isinstance(limits, dict) or set(limits) != set(DEFAULT_LIMITS):
        raise ValueError("Supply every explicit budget limit")
    bounds = {"max_calls": (1,12000 if allow_amended else 3000), "max_calls_per_case": (1,100), "max_output_tokens": (1,4000),
              "max_input_bytes": (2048,500000), "max_runtime_seconds": (1,16200 if allow_amended else 10800)}
    for key, (low, high) in bounds.items():
        if type(limits[key]) is not int or not low <= limits[key] <= high:
            raise ValueError("Invalid explicit budget limit")
    amount = _decimal(limits["max_reserved_usd"])
    ceiling=Decimal("150") if allow_amended else Decimal("10")
    if not 0 < amount <= ceiling:
        raise ValueError("The total exceeds this approval scope")
    return int((amount*1_000_000_000).to_integral_value(rounding=ROUND_CEILING))


def validate_pricing(pricing):
    try:
        verification = pricing["verification"]
        timestamp = datetime.fromisoformat(verification["verified_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc)-timestamp).total_seconds()
        if (verification["method"] != "live_openrouter_models_api" or verification["source_url"] != MODELS_ENDPOINT
                or not _HASH.fullmatch(verification["response_sha256"]) or not -300 <= age <= 86400):
            raise ValueError("Pricing verification missing or stale")
        models = pricing["models"]
        if set(models) != set(APPROVED_MODELS):
            raise ValueError("Unexpected approved models")
        for model, entry in models.items():
            if entry["family"] != model.split('/')[0] or _decimal(entry["request_usd"]) != 0:
                raise ValueError("Unknown model family or nonzero request charge")
            for key in ("prompt_usd_per_million", "completion_usd_per_million"):
                _decimal(entry[key])
        return json.loads(canonical(pricing))
    except (KeyError, ValueError, TypeError, InvalidOperation, AttributeError):
        raise _fail("price_verification_required", "Fresh exact-model pricing from the official provider catalogue is required before paid work.") from None


async def fetch_current_pricing(model_ids=APPROVED_MODELS, *, client_factory=None):
    """Read current token rates and bind the existing zero request-price ceiling.

    The catalogue can omit the optional flat-request price. That omission is
    not an advertised zero: paid dispatch still requires the provider's
    ``max_price.request=0`` filter. An explicit nonzero/invalid charge is never
    accepted. This public GET makes no inference/model request.
    """
    if set(model_ids) != set(APPROVED_MODELS):
        raise ValueError("Only the two reviewed models are permitted")
    import httpx
    if client_factory is None:
        client_factory = httpx.AsyncClient
    reason = "catalogue_schema"
    try:
        reason = "network_error"
        async with client_factory(trust_env=False,follow_redirects=False,timeout=30) as client:
            async with client.stream("GET",MODELS_ENDPOINT) as response:
                if response.status_code != 200:
                    status = response.status_code
                    reason = (f"http_status_{status}" if type(status) is int and 100 <= status <= 599
                              else "http_status_invalid")
                    raise ValueError("Price lookup failed")
                body=bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>10000000:
                        reason = "catalogue_oversized"
                        raise ValueError("Oversized price catalogue")
        reason = "invalid_json"
        data=json.loads(body)
        reason = "catalogue_schema"
        if not isinstance(data,dict) or not isinstance(data.get("data"),list):
            raise ValueError("Invalid catalogue shape")
        models={}
        request_fields={}
        for row in data["data"]:
            if not isinstance(row,dict):raise ValueError("Invalid model row")
            if row.get("id") not in APPROVED_MODELS:continue
            model=row["id"]
            if model in models:
                reason = "duplicate_model_id"
                raise ValueError("Duplicate model price")
            prices=row["pricing"]
            if not isinstance(prices,dict):raise ValueError("Invalid model prices")
            reason = "required_token_price_invalid"
            prompt = _decimal(prices["prompt"])*1000000
            completion = _decimal(prices["completion"])*1000000
            # Validate converted rates against the same ledger price bounds.
            _decimal(prompt.to_eng_string());_decimal(completion.to_eng_string())
            reason = "request_charge_invalid"
            if "request" in prices:
                request_price = _decimal(prices["request"])
                if request_price != 0:
                    raise ValueError("Nonzero request charge")
                request_fields[model] = "explicit_zero"
            else:
                request_price = Decimal("0")
                request_fields[model] = "omitted"
            models[model]={"family":model.split('/')[0],
                "prompt_usd_per_million":str(prompt),
                "completion_usd_per_million":str(completion),
                "request_usd":str(request_price)}
            reason = "catalogue_schema"
        if set(models) != set(APPROVED_MODELS):
            reason = "missing_model_ids"
            raise ValueError("Approved models missing")
        result={"schema_version":1,"verification":{"method":"live_openrouter_models_api","source_url":MODELS_ENDPOINT,
            "verified_at":datetime.now(timezone.utc).isoformat(),"response_sha256":hashlib.sha256(body).hexdigest(),
            "request_charge_policy":{"provider_max_price_request_usd":"0","catalogue_request_fields":request_fields,
                "interpretation":"Omission is not advertised zero; every paid request enforces a zero flat-request price ceiling."}},"models":models}
        return validate_pricing(result)
    except WorkflowError:raise
    except Exception as exc:
        if isinstance(exc,(httpx.TimeoutException,TimeoutError)):
            reason = "network_timeout"
        elif isinstance(exc,httpx.ConnectError):
            reason = "connection_error"
        elif isinstance(exc,httpx.TransportError):
            reason = "network_error"
        # Only fixed categories and a bounded integer HTTP status reach reports.
        # Neither exception messages, HTTP bodies, nor request headers are used.
        raise _fail("price_verification_required", f"Current exact-model prices could not be verified ({reason}). No paid work is permitted.") from None


def approve_budget(answer, pricing, limits):
    """Bind the operator's concrete APPROVE to the shown models, prices and cap."""
    validate_pricing(pricing);_validate_limits(limits)
    if answer.strip() != "APPROVE":
        raise _fail("approval_required", "Enter APPROVE only after reviewing the displayed models, prices and cumulative limits.")
    return {"approved":True,"approval_text":"APPROVE","pricing_identity_sha256":digest(pricing["models"]),
            "limits_sha256":digest(limits),"approved_at":datetime.now(timezone.utc).isoformat()}


class BudgetLedger:
    """One immutable corpus/batch identity, shared by all resumed invocations."""

    def __init__(self, path, batch_id, pricing_manifest, limits, approval):
        if not _valid_id(batch_id):raise ValueError("Invalid fixed evaluation identity")
        self.pricing=validate_pricing(pricing_manifest)
        self._amendment_approval=(json.loads(canonical(approval)) if isinstance(approval,dict)
                                  and approval.get("approval_kind")=="budget_amendment" else None)
        self.limit_nano=_validate_limits(limits,allow_amended=self._amendment_approval is not None)
        self.limits=json.loads(canonical(limits))
        if (not isinstance(approval,dict) or approval.get("approved") is not True or approval.get("approval_text")!="APPROVE"
            or approval.get("pricing_identity_sha256")!=digest(self.pricing["models"])
            or approval.get("limits_sha256")!=digest(limits)):
            raise _fail("approval_required","The exact displayed models, prices and cumulative budget need operator APPROVE before paid work.")
        self.path = Path(path).absolute()
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError("Ledger paths cannot contain symlinks")
        if self._amendment_approval is not None and not self.path.is_file():
            raise _fail("live_ledger_identity","An amended budget may only reopen its existing audited ledger; no new ledger is created.")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists() and not self.path.is_file():
            raise ValueError("Ledger path is not a regular file")
        expected=canonical({"schema_version":34,"batch_id":batch_id,"models":self.pricing["models"],
                            "limits":self.limits,"max_reserved_nano_usd":self.limit_nano})
        self._expected = expected
        self._initializing = True
        existed = self.path.exists()
        if not existed:
            # Exclusive creation is the only permitted fresh-ledger path. An
            # existing empty/truncated file must never replenish the budget.
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
        self._locations = tuple((p, p.stat().st_dev, p.stat().st_ino) for p in (self.path, *self.path.parents))
        with self._connection() as db:
            if existed:
                self._validate_identity(db)
            else:
                db.executescript("""
                    CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE cases (
                        case_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                        started_at REAL NOT NULL, finished_at REAL);
                    CREATE TABLE calls (
                        call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        case_id TEXT NOT NULL REFERENCES cases(case_id), model TEXT NOT NULL,
                        input_allowance INTEGER NOT NULL, output_allowance INTEGER NOT NULL,
                        reserved_nano_usd INTEGER NOT NULL, status TEXT NOT NULL,
                        prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
                        actual_nano_usd INTEGER, error_code TEXT,
                        started_at REAL NOT NULL, finished_at REAL);
                """)
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO metadata VALUES ('identity', ?)", (expected,))
                db.execute("INSERT INTO metadata VALUES ('started_at', ?)", (str(time.time()),))
                db.commit()
        self._initializing = False
        os.chmod(self.path, 0o600)

    def _validate_identity(self, db):
        try:
            required = {
                "metadata": ["name", "value"],
                "cases": ["case_id", "status", "started_at", "finished_at"],
                "calls": ["call_id", "case_id", "model", "input_allowance", "output_allowance", "reserved_nano_usd", "status", "prompt_tokens", "completion_tokens", "total_tokens", "actual_nano_usd", "error_code", "started_at", "finished_at"],
            }
            for table, columns in required.items():
                if [r[1] for r in db.execute("PRAGMA table_info(" + table + ")")] != columns:
                    raise ValueError("Missing ledger schema")
            row = db.execute("SELECT value FROM metadata WHERE name='identity'").fetchone()
            if row is None or row[0] != self._expected:
                raise ValueError("Changed ledger identity")
            if self._amendment_approval is not None:
                record=_read_amendment_record(db)
                if record is None or record["new_scoped_approval"]!=self._amendment_approval:
                    raise ValueError("The approved amendment is missing or changed")
                if canonical(record["new_identity"])!=self._expected:
                    raise ValueError("The amendment identity differs")
                _ledger_snapshot(db)  # Reject semantically impossible saved usage before any new dispatch.
        except (sqlite3.DatabaseError, ValueError):
            raise _fail("live_ledger_identity", "The saved evaluation ledger is incomplete or has a different identity. No model call was made.") from None

    def _check_locations(self):
        try:
            for path, device, inode in self._locations:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or (info.st_dev, info.st_ino) != (device, inode):
                    raise ValueError("Ledger location changed")
        except (OSError, ValueError):
            raise _fail("live_ledger_identity", "The saved evaluation ledger or its location changed. No model call was made.") from None

    @contextmanager
    def _connection(self):
        self._check_locations()
        # mode=rw is essential: sqlite's default silently creates a new file
        # after deletion, which would erase the cumulative reservation history.
        try:
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", timeout=15, isolation_level=None, uri=True)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=15000")
        except sqlite3.DatabaseError:
            raise _fail("live_ledger_identity", "The saved evaluation ledger could not be opened safely.") from None
        try:
            self._check_locations()
            if not self._initializing:
                self._validate_identity(db)
            yield db
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def begin_case(self, case_id):
        if not _valid_id(case_id):
            raise ValueError("Invalid case identity")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if row:
                if row[0] == "in_progress":
                    raise _fail("live_interrupted_case", "An earlier attempt for this case is incomplete. It must not be retried automatically.")
                return False
            if db.execute("SELECT 1 FROM metadata WHERE name='poison'").fetchone():
                raise _fail("live_accounting_error", "The saved ledger is stopped after a provider/accounting failure.")
            db.execute("INSERT INTO cases(case_id,status,started_at) VALUES (?, 'in_progress', ?)", (case_id, time.time()))
            db.commit()
            return True

    def finish_case(self, case_id, status="complete"):
        if not _valid_id(case_id) or not _valid_id(status) or status == "in_progress":
            raise ValueError("Invalid final case status")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown case")
            if row[0] != "in_progress":
                if row[0] != status:
                    raise ValueError("Finished cases are immutable")
                return
            db.execute("UPDATE cases SET status=?,finished_at=? WHERE case_id=?", (status, time.time(), case_id))
            db.commit()

    def case_states(self):
        with self._connection() as db:
            return {row["case_id"]: row["status"] for row in db.execute("SELECT case_id,status FROM cases ORDER BY case_id")}

    def reserve(self, case_id, input_allowance, output_allowance, model):
        if not _valid_id(case_id) or type(input_allowance) is not int or not 2048 <= input_allowance <= 1_000_000:
            raise ValueError("Invalid model reservation")
        validate_pricing(self.pricing)
        if model not in self.pricing["models"] or type(output_allowance) is not int or not 1<=output_allowance<=self.limits["max_output_tokens"]:
            raise _fail("live_config_mismatch","Unapproved model or output allowance")
        prices=self.pricing["models"][model]
        amount=int(((_decimal(prices["prompt_usd_per_million"])*input_allowance+
                     _decimal(prices["completion_usd_per_million"])*output_allowance)*1000).to_integral_value(rounding=ROUND_CEILING))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM metadata WHERE name='poison'").fetchone():
                raise _fail("live_accounting_error", "The saved ledger is stopped after a provider/accounting failure.")
            case = db.execute("SELECT status FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if not case or case[0] != "in_progress":
                raise _fail("live_interrupted_case", "The case has not been started or was already finalized. No model call was made.")
            start=db.execute("SELECT value FROM metadata WHERE name='started_at'").fetchone()
            if start is None or not -300<=time.time()-float(start[0])<=self.limits["max_runtime_seconds"]:
                raise _fail("live_time_limit","The approved cumulative experiment time limit has expired. No model call was made.")
            counts = db.execute("SELECT COUNT(*),COALESCE(SUM(reserved_nano_usd),0) FROM calls").fetchone()
            per_case = db.execute("SELECT COUNT(*) FROM calls WHERE case_id=?", (case_id,)).fetchone()[0]
            if counts[0] >= self.limits["max_calls"] or per_case >= self.limits["max_calls_per_case"] or counts[1] + amount > self.limit_nano:
                raise _fail("live_budget_exhausted", "The fixed cumulative request or reservation limit has been reached. No model call was made.")
            cur = db.execute("INSERT INTO calls(case_id,model,input_allowance,output_allowance,reserved_nano_usd,status,started_at) VALUES (?,?,?,?,?, 'reserved',?)",
                             (case_id, model, input_allowance, output_allowance, amount, time.time()))
            call_id = cur.lastrowid
            db.commit()
            return {"call_id": call_id, "input_allowance": input_allowance, "output_allowance": output_allowance, "reserved_nano_usd": amount}

    def complete_call(self, reservation, usage, status="complete", error_code=None):
        """Validate and retain usage even for a truncated/otherwise unusable response."""
        names = ("prompt_tokens", "completion_tokens", "total_tokens")
        valid = isinstance(usage, dict) and all(type(usage.get(n)) is int and usage[n] >= 0 for n in names)
        actual = None
        if valid:
            valid = (usage["prompt_tokens"] <= reservation["input_allowance"]
                     and usage["completion_tokens"] <= reservation["output_allowance"]
                     and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
            if usage.get("cost") is None:
                valid=False
            else:
                try:
                    if type(usage["cost"]) not in (int, float, str):
                        raise ValueError("Invalid cost")
                    cost = Decimal(str(usage["cost"]))
                    if not cost.is_finite() or cost < 0 or cost > Decimal(10):
                        raise ValueError("Invalid cost")
                    actual = int((cost * Decimal(1_000_000_000)).to_integral_value(rounding=ROUND_CEILING))
                    valid = valid and actual <= reservation["reserved_nano_usd"]
                except (InvalidOperation, ValueError, OverflowError):
                    valid = False
        if not valid:
            self.fail_call(reservation["call_id"], "live_accounting_error", poison=True)
            raise _fail("live_accounting_error", "Provider usage exceeded the reservation or was invalid. Further model calls are stopped.")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("UPDATE calls SET status=?,prompt_tokens=?,completion_tokens=?,total_tokens=?,actual_nano_usd=?,error_code=?,finished_at=? WHERE call_id=? AND status='reserved'",
                             (status, usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"], actual, error_code, time.time(), reservation["call_id"]))
            if cur.rowcount != 1:
                raise ValueError("Model reservation already finalized")
            db.commit()

    def fail_call(self, call_id, code, *, poison):
        if type(call_id) is not int or not _valid_id(code):
            raise ValueError("Invalid failure metadata")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE calls SET status='error',error_code=?,finished_at=? WHERE call_id=? AND status='reserved'", (code, time.time(), call_id))
            if poison:
                db.execute("INSERT OR IGNORE INTO metadata(name,value) VALUES ('poison',?)", (code,))
            db.commit()

    def stats(self):
        with self._connection() as db:
            rows = list(db.execute("SELECT * FROM calls ORDER BY call_id"))
            identity = json.loads(db.execute("SELECT value FROM metadata WHERE name='identity'").fetchone()[0])
            poison = db.execute("SELECT value FROM metadata WHERE name='poison'").fetchone()
            started = db.execute("SELECT value FROM metadata WHERE name='started_at'").fetchone()
            amendment = _read_amendment_record(db) if self._amendment_approval is not None else None
        total = sum(r["reserved_nano_usd"] for r in rows)
        actual = sum(r["actual_nano_usd"] for r in rows if r["actual_nano_usd"] is not None)
        return {**identity, "reserved_requests": len(rows), "reserved_nano_usd": total,
                "reserved_usd": total / 1_000_000_000,
                "known_actual_nano_usd": actual, "known_actual_usd": actual / 1_000_000_000,
                "requests_without_actual_cost": sum(r["actual_nano_usd"] is None for r in rows),
                "in_flight_requests": sum(r["status"] == "reserved" for r in rows),
                "poisoned": poison is not None, "stop_code": poison[0] if poison else None,
                "case_states": self.case_states(),
                "calls": [dict(r) for r in rows],
                "original_started_at":started[0] if started else None,
                "budget_amendment":amendment,
                "cost_scope": "Non-refundable conservative local reservation and provider price filters; known actual API usage excludes unknown usage and account top-up fees."}


class BudgetClient:
    def __init__(self, key, ledger, case_id, *, purpose="experiment", client_factory=None):
        if not isinstance(key, str) or not key.strip():
            raise _fail("missing_model_key", "OPENROUTER_API_KEY is missing from the existing environment or .env.")
        self._key, self.ledger, self._client_factory = key, ledger, client_factory
        if not _valid_id(case_id):raise ValueError("Invalid model case identity")
        self.case_id, self.purpose = case_id, purpose

    async def complete(self, messages, config):
        model=config.get("model")
        output=config.get("max_output_tokens")
        if (model not in self.ledger.pricing["models"] or config.get("temperature")!=0
            or type(output) is not int or not 1<=output<=self.ledger.limits["max_output_tokens"]):
            raise _fail("live_config_mismatch","The model or output allowance differs from the approved bounds.")
        response_format=config.get("_response_format",{"type":"json_object"})
        if response_format!={"type":"json_object"}:response_format=_critic_response_format(config)
        if (not isinstance(messages, list) or not messages
                or any(not isinstance(m, dict) or set(m) != {"role", "content"}
                       or m["role"] not in ("system", "user", "assistant") or not isinstance(m["content"], str)
                       for m in messages)):
            raise _fail("live_config_mismatch", "Only text messages are permitted for this evaluation.")
        serialized = canonical(messages)
        if self._key in serialized or self._key in canonical(response_format):
            raise _fail("live_sensitive_input", "The request unexpectedly contained the model credential. No model call was made.")
        allowance = len(serialized.encode("utf-8")) + len(canonical(response_format).encode("utf-8")) + 2048
        if allowance>self.ledger.limits["max_input_bytes"]:
            raise _fail("live_config_mismatch","The request exceeded the approved input byte allowance.")
        reservation = self.ledger.reserve(self.case_id, allowance, output, model)
        prices=self.ledger.pricing["models"][model]
        payload = {"model": model, "messages": messages, "temperature": 0, "max_tokens": output,
                   "response_format": response_format, "usage": {"include": True},
                   "provider": {"allow_fallbacks": False, "require_parameters": True,
                                "max_price": {"prompt": float(prices["prompt_usd_per_million"]), "completion": float(prices["completion_usd_per_million"]), "request": 0}}}
        try:
            if self._client_factory is None:
                import httpx
                factory = httpx.AsyncClient
            else:
                factory = self._client_factory
            async with factory(trust_env=False, follow_redirects=False, timeout=TIMEOUT_SECONDS) as client:
                async with client.stream("POST", ENDPOINT, headers={"Authorization": "Bearer " + self._key}, json=payload) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise _fail("live_response_oversized", "The model response exceeded the fixed response-size limit.")
                    if response.status_code < 200 or response.status_code >= 300:
                        provider_code = None
                        try:
                            candidate = json.loads(body).get("error", {}).get("code")
                            known = {"invalid_request_error", "invalid_json_schema", "invalid_schema", "unsupported_parameter",
                                     "rate_limit_exceeded", "insufficient_quota", "provider_error"}
                            if (type(candidate) is int and 0 <= candidate <= 999999) or (type(candidate) is str and candidate in known):
                                provider_code = candidate
                        except (ValueError, AttributeError):
                            pass
                        error = _fail("model_http_error", "The provider rejected the request. The fixed reservation is retained and further calls are stopped.")
                        # Agents.ask records these two safe fields on HTTP
                        # failures. Never attach the provider's raw body.
                        error.http_status = response.status_code
                        error.provider_error_code = provider_code
                        raise error
            data = json.loads(body)
            if not isinstance(data, dict) or data.get("model") != model:
                raise _fail("live_accounting_error", "The provider returned an unexpected model. Further calls are stopped.")
            choice = data["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise _fail("model_unavailable", "The provider did not return text content.")
            finish = choice.get("finish_reason")
            self.ledger.complete_call(reservation, data.get("usage"), status="complete" if finish == "stop" else "incomplete",
                                      error_code=None if finish == "stop" else "model_response_incomplete")
            if finish != "stop":
                raise _fail("model_response_incomplete", "The model response did not finish normally. Its usage is retained and this case is unevaluable.")
            return {"content": content.replace(self._key, "[REDACTED_MODEL_CREDENTIAL]"),
                    "model": model, "usage": {k: data["usage"].get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")}}
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                # The call may have been billed. Keep its complete reservation
                # and leave its case in progress so resume can mark it
                # interrupted/unevaluable, never replay it. Other frozen cases
                # can still use the remaining cumulative budget.
                self.ledger.fail_call(reservation["call_id"], "live_interrupted_dispatch", poison=False)
                raise
            code = getattr(exc, "code", None)
            if not _valid_id(code):
                code = "model_unavailable"
            # A validated truncated response is a case error, not an accounting
            # anomaly. All other unknown outcomes stop the complete paid batch.
            poison = code != "model_response_incomplete"
            self.ledger.fail_call(reservation["call_id"], code, poison=poison)
            if isinstance(exc, WorkflowError):
                raise
            raise _fail("model_unavailable", "The provider response was unavailable or invalid. The reservation is retained and further calls are stopped.") from None


# Completion scope is a cumulative total, never an additional/refunded allowance.
COMPLETION_LIMITS = {"max_calls":12000,"max_calls_per_case":50,"max_reserved_usd":"150.00",
                     "max_output_tokens":2200,"max_input_bytes":100000,"max_runtime_seconds":16200}
_BASE_COLUMNS = {
    "metadata":["name","value"],
    "cases":["case_id","status","started_at","finished_at"],
    "calls":["call_id","case_id","model","input_allowance","output_allowance","reserved_nano_usd","status",
             "prompt_tokens","completion_tokens","total_tokens","actual_nano_usd","error_code","started_at","finished_at"]}
_AMENDMENT_TRIGGERS = {
    "budget_amendments_no_update":"CREATE TRIGGER budget_amendments_no_update BEFORE UPDATE ON budget_amendments BEGIN SELECT RAISE(ABORT, 'Budget amendment history is immutable'); END",
    "budget_amendments_no_delete":"CREATE TRIGGER budget_amendments_no_delete BEFORE DELETE ON budget_amendments BEGIN SELECT RAISE(ABORT, 'Budget amendment history is immutable'); END",
    "budget_amendments_insert_once":"CREATE TRIGGER budget_amendments_insert_once BEFORE INSERT ON budget_amendments WHEN EXISTS(SELECT 1 FROM budget_amendments) BEGIN SELECT RAISE(ABORT, 'Only the single approved completion amendment is permitted'); END"}


def _amendment_error(message):
    return _fail("live_budget_amendment_invalid",message)


def _identity(batch_id,pricing,limits,*,amended=False):
    return {"schema_version":34,"batch_id":batch_id,"models":pricing["models"],"limits":limits,
            "max_reserved_nano_usd":_validate_limits(limits,allow_amended=amended)}


def _validate_amendment_inputs(batch_id,pricing,old_limits,old_approval,new_limits,plan_sha256):
    if not _valid_id(batch_id) or not isinstance(plan_sha256,str) or not _HASH.fullmatch(plan_sha256):
        raise _amendment_error("An exact immutable completion plan hash and original batch identity are required.")
    prices=validate_pricing(pricing)
    _validate_limits(old_limits);_validate_limits(new_limits,allow_amended=True)
    if (not isinstance(old_approval,dict) or old_approval.get('approved') is not True
        or old_approval.get('approval_text')!='APPROVE' or old_approval.get('approval_kind')=='budget_amendment'
        or old_approval.get('pricing_identity_sha256')!=digest(prices['models'])
        or old_approval.get('limits_sha256')!=digest(old_limits)):
        raise _amendment_error("The original saved approval does not bind the original prices and limits.")
    for key in ('max_calls_per_case','max_output_tokens','max_input_bytes'):
        if new_limits[key]!=old_limits[key] or new_limits[key]!=COMPLETION_LIMITS[key]:
            raise _amendment_error("Per-case, output and input bounds must retain the original reviewed limits.")
    if new_limits['max_calls']<old_limits['max_calls'] or new_limits['max_runtime_seconds']<old_limits['max_runtime_seconds']:
        raise _amendment_error("A completion amendment cannot reduce the original request or time allowance.")
    if _decimal(new_limits['max_reserved_usd'])<_decimal(old_limits['max_reserved_usd']):
        raise _amendment_error("A completion amendment cannot reduce the original cumulative dollar allowance.")
    return prices,_identity(batch_id,prices,old_limits),_identity(batch_id,prices,new_limits,amended=True)


def _verify_base_schema(db):
    try:
        for name,columns in _BASE_COLUMNS.items():
            if [r[1] for r in db.execute('PRAGMA table_info('+name+')')]!=columns:
                raise ValueError('Missing original schema')
        if db.execute('PRAGMA quick_check(1)').fetchone()[0]!='ok':raise ValueError('Invalid SQLite ledger')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise ValueError('Unbound call history')
        metadata={row['name']:row['value'] for row in db.execute('SELECT name,value FROM metadata')}
        if 'identity' not in metadata or 'started_at' not in metadata:raise ValueError('Missing original identity or start')
        start=float(metadata['started_at'])
        if not 0<start<float('inf'):raise ValueError('Invalid original start')
        return metadata
    except (sqlite3.DatabaseError,ValueError,TypeError,KeyError):
        raise _fail('live_ledger_identity','The original ledger is missing, truncated or structurally invalid. No amendment was applied.') from None


@contextmanager
def _existing_amendment_connection(path,*,write=False):
    location=Path(path).absolute()
    try:
        if not location.is_file() or location.stat().st_size==0 or any(p.is_symlink() for p in (location,*location.parents)):
            raise ValueError('Missing original file')
        places=tuple((p,p.stat().st_dev,p.stat().st_ino) for p in (location,*location.parents))
        db=sqlite3.connect(location.as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=15,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=15000')
    except (OSError,ValueError,sqlite3.DatabaseError):
        raise _fail('live_ledger_identity','The existing original budget ledger could not be opened; no replacement ledger was created.') from None
    try:
        for p,device,inode in places:
            info=p.lstat()
            if stat.S_ISLNK(info.st_mode) or (info.st_dev,info.st_ino)!=(device,inode):raise ValueError('Ledger location changed')
        if write:db.execute('BEGIN IMMEDIATE')
        _verify_base_schema(db)
        yield db
        for p,device,inode in places:
            info=p.lstat()
            if stat.S_ISLNK(info.st_mode) or (info.st_dev,info.st_ino)!=(device,inode):raise ValueError('Ledger location changed')
    finally:
        if db.in_transaction:db.rollback()
        db.close()


def _validate_saved_accounting(identity,calls,cases,sequence):
    """Reject states the trusted frozen reserve/complete/fail code cannot create."""
    try:
        limits=identity['limits'];models=identity['models']
        total_cap=_validate_limits(limits,allow_amended=True)
        if identity.get('max_reserved_nano_usd')!=total_cap:raise ValueError('Inconsistent total cap')
        case_map={}
        def timestamp(value):
            return type(value) in (int,float) and 0<value<float('inf')
        for row in cases:
            if not _valid_id(row['case_id']) or not _valid_id(row['status']) or not timestamp(row['started_at']):
                raise ValueError('Invalid saved case')
            if row['status']=='in_progress':
                if row['finished_at'] is not None:raise ValueError('Active case has finish time')
            elif not timestamp(row['finished_at']):raise ValueError('Final case lacks finish time')
            case_map[row['case_id']]=row
        if [r['call_id'] for r in calls]!=list(range(1,len(calls)+1)):
            raise ValueError('Original call IDs contain a gap or invalid value')
        expected_sequence=[{'name':'calls','seq':len(calls)}] if calls else []
        if sequence!=expected_sequence:raise ValueError('Original call sequence changed')
        per_case={};reserved_total=0
        for row in calls:
            if row['case_id'] not in case_map or row['model'] not in models:raise ValueError('Unbound call scope')
            if (type(row['input_allowance']) is not int or not 2048<=row['input_allowance']<=1_000_000
                or type(row['output_allowance']) is not int or not 1<=row['output_allowance']<=limits['max_output_tokens']):
                raise ValueError('Invalid recorded allowances')
            prices=models[row['model']]
            expected=int(((_decimal(prices['prompt_usd_per_million'])*row['input_allowance']+
                _decimal(prices['completion_usd_per_million'])*row['output_allowance'])*1000).to_integral_value(rounding=ROUND_CEILING))
            if type(row['reserved_nano_usd']) is not int or row['reserved_nano_usd']!=expected:
                raise ValueError('Recorded reservation differs from its frozen price and allowances')
            if not timestamp(row['started_at']):raise ValueError('Invalid call time')
            if row['error_code'] is not None and not _valid_id(row['error_code']):raise ValueError('Invalid stored error code')
            usage=[row[key] for key in ('prompt_tokens','completion_tokens','total_tokens')]
            status=row['status']
            if status in ('reserved','error'):
                if any(value is not None for value in usage) or row['actual_nano_usd'] is not None:
                    raise ValueError('Unknown-outcome call has invented usage')
                if status=='reserved' and (row['finished_at'] is not None or row['error_code'] is not None):
                    raise ValueError('Reserved call is unexpectedly finalized')
                if status=='error' and (not timestamp(row['finished_at']) or row['error_code'] is None):
                    raise ValueError('Failed call lacks its saved outcome')
            elif status in ('complete','incomplete'):
                if (any(type(value) is not int or value<0 for value in usage)
                    or usage[0]>row['input_allowance'] or usage[1]>row['output_allowance'] or usage[2]!=usage[0]+usage[1]
                    or type(row['actual_nano_usd']) is not int or not 0<=row['actual_nano_usd']<=expected
                    or not timestamp(row['finished_at'])):
                    raise ValueError('Completed usage exceeds or contradicts its reservation')
            else:raise ValueError('Unknown call status')
            per_case[row['case_id']]=per_case.get(row['case_id'],0)+1
            reserved_total+=expected
        if (len(calls)>limits['max_calls'] or reserved_total>total_cap
            or any(count>limits['max_calls_per_case'] for count in per_case.values())):
            raise ValueError('Original accounting exceeds its approved total or case caps')
    except (ValueError,TypeError,KeyError,InvalidOperation):
        raise _fail('live_ledger_identity','Saved ledger usage, reservations, call IDs or approved totals are inconsistent. No amendment or new dispatch is permitted.') from None


def _ledger_snapshot(db):
    metadata=_verify_base_schema(db)
    calls=[dict(row) for row in db.execute('SELECT * FROM calls ORDER BY call_id')]
    cases=[dict(row) for row in db.execute('SELECT * FROM cases ORDER BY case_id')]
    sequence=[dict(row) for row in db.execute("SELECT name,seq FROM sqlite_sequence WHERE name='calls'")]
    for row in calls:
        if type(row['reserved_nano_usd']) is not int or row['reserved_nano_usd']<0:
            raise _fail('live_ledger_identity','Invalid non-refundable reservation history.')
        if row['actual_nano_usd'] is not None and (type(row['actual_nano_usd']) is not int or row['actual_nano_usd']<0):
            raise _fail('live_ledger_identity','Invalid actual usage history.')
    identity=json.loads(metadata['identity'])
    _validate_saved_accounting(identity,calls,cases,sequence)
    reserved=sum(row['reserved_nano_usd'] for row in calls)
    actual=sum(row['actual_nano_usd'] for row in calls if row['actual_nano_usd'] is not None)
    return {**identity,'reserved_requests':len(calls),'reserved_nano_usd':reserved,'reserved_usd':reserved/1_000_000_000,
        'known_actual_nano_usd':actual,'known_actual_usd':actual/1_000_000_000,
        'requests_without_actual_cost':sum(row['actual_nano_usd'] is None for row in calls),
        'in_flight_requests':sum(row['status']=='reserved' for row in calls),
        'poisoned':'poison' in metadata,'stop_code':metadata.get('poison'),
        'case_states':{row['case_id']:row['status'] for row in cases},'original_started_at':metadata['started_at'],
        'calls_rows_sha256':digest(calls),'case_rows_sha256':digest(cases),'call_sequence_sha256':digest(sequence),
        'prior_state_sha256':digest({'metadata':metadata,'calls':calls,'cases':cases,'call_sequence':sequence})}


def _read_amendment_record(db):
    try:
        metadata={r['name']:r['value'] for r in db.execute('SELECT name,value FROM metadata')}
        table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='budget_amendments'").fetchone()
        if table is None:
            if any(key in metadata for key in ('active_budget_amendment','original_budget_identity','original_budget_approval')):
                raise ValueError('Partial amendment metadata')
            return None
        if [r[1] for r in db.execute('PRAGMA table_info(budget_amendments)')]!=['amendment_id','record_json','record_sha256']:
            raise ValueError('Invalid amendment history schema')
        records=list(db.execute('SELECT amendment_id,record_json,record_sha256 FROM budget_amendments'))
        if len(records)!=1:raise ValueError('Missing or duplicate amendment history')
        row=records[0];record=json.loads(row['record_json'])
        if canonical(record)!=row['record_json'] or digest(record)!=row['record_sha256'] or record['amendment_id']!=row['amendment_id']:
            raise ValueError('Changed amendment record')
        if metadata.get('active_budget_amendment')!=record['amendment_id']:
            raise ValueError('Changed active amendment')
        if metadata.get('original_budget_identity')!=canonical(record['old_identity']):raise ValueError('Original identity changed')
        if metadata.get('original_budget_approval')!=canonical(record['old_approval']):raise ValueError('Original approval changed')
        if metadata.get('identity')!=canonical(record['new_identity']):raise ValueError('Active identity changed')
        if metadata.get('started_at')!=record['original_started_at']:raise ValueError('Original time anchor changed')
        if (digest(record['old_identity'])!=record['old_identity_sha256'] or digest(record['new_identity'])!=record['new_identity_sha256']
            or digest(record['old_approval'])!=record['old_approval_sha256']
            or digest(record['prior_stats'])!=record['prior_stats_sha256']
            or record['old_limits']!=record['old_identity']['limits'] or record['new_limits']!=record['new_identity']['limits']):
            raise ValueError('Amendment bindings changed')
        expected_id=digest({'old_identity':record['old_identity'],'new_identity':record['new_identity'],
            'plan_sha256':record['plan_sha256'],'old_approval_sha256':record['old_approval_sha256']})
        if record['amendment_id']!=expected_id:raise ValueError('Amendment ID binding changed')
        approval=record['new_scoped_approval']
        if (approval.get('approval_kind')!='budget_amendment' or approval.get('approval_text')!='APPROVE' or approval.get('approved') is not True
            or approval.get('plan_sha256')!=record['plan_sha256'] or approval.get('limits_sha256')!=digest(record['new_limits'])
            or approval.get('pricing_identity_sha256')!=digest(record['new_identity']['models'])
            or approval.get('old_approval_sha256')!=record['old_approval_sha256']
            or approval.get('approved_at')!=record['approved_at']):raise ValueError('Scoped approval changed')
        for name,sql in _AMENDMENT_TRIGGERS.items():
            trigger=db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
            if trigger is None or trigger[0]!=sql:raise ValueError('Immutable-history guard changed')
        return record
    except (sqlite3.DatabaseError,ValueError,TypeError,KeyError):
        raise _fail('live_ledger_identity','The immutable budget-amendment audit record is incomplete or changed.') from None


def _validate_existing_transition(db,old_identity,new_identity,old_approval,plan_sha256):
    record=_read_amendment_record(db)
    if record is not None:
        if (record['old_identity']!=old_identity or record['new_identity']!=new_identity
            or record['old_approval']!=old_approval or record['plan_sha256']!=plan_sha256):
            raise _amendment_error('An amendment already exists for different limits, prices or plan. Its allowance cannot be replayed or repurposed.')
        return record
    metadata=_verify_base_schema(db)
    if metadata['identity']!=canonical(old_identity):
        raise _fail('live_ledger_identity','The original ledger identity, prices or approved limits do not match.')
    return None


def preview_budget_amendment(path,batch_id,pricing,old_limits,old_approval,new_limits,plan_sha256):
    """Read-only concrete preview, including current spent/reserved totals."""
    prices,old_identity,new_identity=_validate_amendment_inputs(batch_id,pricing,old_limits,old_approval,new_limits,plan_sha256)
    with _existing_amendment_connection(path) as db:
        record=_validate_existing_transition(db,old_identity,new_identity,old_approval,plan_sha256)
        snapshot=_ledger_snapshot(db)
    if new_identity['max_reserved_nano_usd']<snapshot['reserved_nano_usd'] or new_limits['max_calls']<snapshot['reserved_requests']:
        raise _amendment_error('The proposed total is below already consumed non-refundable reservations or requests.')
    deadline=float(snapshot['original_started_at'])+new_limits['max_runtime_seconds']
    return {'status':'already_amended' if record else 'awaiting_explicit_approval','plan_sha256':plan_sha256,
        'current_stats':snapshot,'old_limits':json.loads(canonical(old_limits)),'new_limits':json.loads(canonical(new_limits)),
        'old_approval_sha256':digest(old_approval),'original_started_at':snapshot['original_started_at'],
        'new_deadline_utc':datetime.fromtimestamp(deadline,timezone.utc).isoformat(),'remaining_seconds':max(0,deadline-time.time()),
        'time_anchor_reset':False,'additional_allowance':False,'receipt':record,
        'new_scoped_approval':record['new_scoped_approval'] if record else None}


def approve_budget_amendment(answer,pricing,old_limits,old_approval,new_limits,plan_sha256):
    """New approval is distinct from the original saved S40 approval."""
    validate_pricing(pricing);_validate_limits(old_limits);_validate_limits(new_limits,allow_amended=True)
    if not isinstance(answer,str) or answer.strip()!='APPROVE' or not isinstance(plan_sha256,str) or not _HASH.fullmatch(plan_sha256):
        raise _fail('approval_required','The displayed exact completion plan and cumulative amended budget require a new plain APPROVE.')
    return {'approved':True,'approval_text':'APPROVE','approval_kind':'budget_amendment','plan_sha256':plan_sha256,
        'pricing_identity_sha256':digest(pricing['models']),'limits_sha256':digest(new_limits),
        'old_approval_sha256':digest(old_approval),'approved_at':datetime.now(timezone.utc).isoformat()}


def amend_existing_budget(path,batch_id,pricing,old_limits,old_approval,new_limits,plan_sha256,answer=None):
    """Atomically amend one existing ledger, returning (ledger, immutable receipt).

    The master must hold its process-level run lock before this operation. No
    cases, calls, usage rows, poison flags, sequence IDs or start times are reset.
    A matching prior amendment can be reopened with answer=None; changed plans
    and changed totals are rejected even if another APPROVE is supplied.
    """
    prices,old_identity,new_identity=_validate_amendment_inputs(batch_id,pricing,old_limits,old_approval,new_limits,plan_sha256)
    if answer is not None and (not isinstance(answer,str) or answer.strip()!='APPROVE'):
        raise _fail('approval_required','Only the explicit displayed-plan APPROVE authorizes a new amendment.')
    with _existing_amendment_connection(path,write=True) as db:
        record=_validate_existing_transition(db,old_identity,new_identity,old_approval,plan_sha256)
        if record is None:
            approval=approve_budget_amendment(answer,prices,old_limits,old_approval,new_limits,plan_sha256)
            prior=_ledger_snapshot(db)
            if new_identity['max_reserved_nano_usd']<prior['reserved_nano_usd'] or new_limits['max_calls']<prior['reserved_requests']:
                raise _amendment_error('The amended total is below consumed reservations or requests.')
            elapsed=time.time()-float(prior['original_started_at'])
            if not -300<=elapsed<=new_limits['max_runtime_seconds']:
                raise _fail('live_time_limit','The amended deadline has already expired relative to the original start. The time anchor will not be reset.')
            amendment_id=digest({'old_identity':old_identity,'new_identity':new_identity,'plan_sha256':plan_sha256,'old_approval_sha256':digest(old_approval)})
            record={'schema_version':1,'amendment_id':amendment_id,'plan_sha256':plan_sha256,
                'old_identity':old_identity,'old_identity_sha256':digest(old_identity),'new_identity':new_identity,'new_identity_sha256':digest(new_identity),
                'old_limits':json.loads(canonical(old_limits)),'new_limits':json.loads(canonical(new_limits)),
                'old_approval':json.loads(canonical(old_approval)),'old_approval_sha256':digest(old_approval),
                'new_scoped_approval':approval,'approved_at':approval['approved_at'],
                'original_started_at':prior['original_started_at'],'time_anchor_reset':False,
                'prior_stats':prior,'prior_stats_sha256':digest(prior),'all_prior_reservations_retained':True,
                'scope':'Cumulative total limits for one exact completion plan; not additional spend and not a replay allowance.'}
            db.execute('CREATE TABLE budget_amendments (amendment_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL)')
            for sql in _AMENDMENT_TRIGGERS.values():db.execute(sql)
            db.execute('INSERT INTO budget_amendments VALUES (?,?,?)',(amendment_id,canonical(record),digest(record)))
            for name,value in (('original_budget_identity',canonical(old_identity)),('original_budget_approval',canonical(old_approval)),('active_budget_amendment',amendment_id)):
                db.execute('INSERT INTO metadata(name,value) VALUES (?,?)',(name,value))
            changed=db.execute("UPDATE metadata SET value=? WHERE name='identity' AND value=?",(canonical(new_identity),canonical(old_identity)))
            if changed.rowcount!=1:raise _amendment_error('Original ledger identity changed during approval')
            after=_ledger_snapshot(db)
            for key in ('calls_rows_sha256','case_rows_sha256','call_sequence_sha256','original_started_at','reserved_requests','reserved_nano_usd','known_actual_nano_usd','poisoned','stop_code'):
                if after[key]!=prior[key]:raise _amendment_error('A protected original ledger value changed during amendment')
            if _read_amendment_record(db)!=record:raise _amendment_error('Amendment audit failed before commit')
            db.commit()
        else:
            db.rollback()
    ledger=BudgetLedger(path,batch_id,prices,new_limits,record['new_scoped_approval'])
    return ledger,json.loads(canonical(record))
