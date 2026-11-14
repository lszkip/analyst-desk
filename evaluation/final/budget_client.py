"""Sequence 34 approved multi-experiment bounded text client and non-refundable durable cost ledger.

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


def _validate_limits(limits):
    if not isinstance(limits, dict) or set(limits) != set(DEFAULT_LIMITS):
        raise ValueError("Supply every explicit budget limit")
    bounds = {"max_calls": (1,3000), "max_calls_per_case": (1,100), "max_output_tokens": (1,4000),
              "max_input_bytes": (2048,500000), "max_runtime_seconds": (1,10800)}
    for key, (low, high) in bounds.items():
        if type(limits[key]) is not int or not low <= limits[key] <= high:
            raise ValueError("Invalid explicit budget limit")
    amount = _decimal(limits["max_reserved_usd"])
    if not 0 < amount <= Decimal("10"):
        raise ValueError("The approved total must be positive and no more than ten USD")
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
        self.limit_nano=_validate_limits(limits)
        self.limits=json.loads(canonical(limits))
        if (not isinstance(approval,dict) or approval.get("approved") is not True or approval.get("approval_text")!="APPROVE"
            or approval.get("pricing_identity_sha256")!=digest(self.pricing["models"])
            or approval.get("limits_sha256")!=digest(limits)):
            raise _fail("approval_required","The exact displayed models, prices and cumulative budget need operator APPROVE before paid work.")
        self.path = Path(path).absolute()
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError("Ledger paths cannot contain symlinks")
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
