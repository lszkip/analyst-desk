"""Durable budgets, evidence and scrubbed event records; separate from checkpoints."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time

from workflow_contracts import WorkflowError, canonical, digest


class RunStore:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("Workflow storage cannot be a symlink.")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, config_hash TEXT NOT NULL, token_limit INTEGER NOT NULL,
                    tool_limit INTEGER NOT NULL, reserved_tokens INTEGER NOT NULL DEFAULT 0,
                    tool_calls INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS evidence (
                    run TEXT NOT NULL, id TEXT NOT NULL, content TEXT NOT NULL,
                    PRIMARY KEY(run,id));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL,
                    kind TEXT NOT NULL, role TEXT NOT NULL, details TEXT NOT NULL, at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS responses (
                    run TEXT NOT NULL, id TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY(run,id));
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def start(self, run, config):
        with self.db(True) as db:
            existing = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
            if existing and existing["config_hash"] != digest(config):
                raise WorkflowError("changed_configuration", "Resume requires the original workflow configuration.")
            db.execute("INSERT OR IGNORE INTO runs(id,config_hash,token_limit,tool_limit) VALUES(?,?,?,?)",
                       (run, digest(config), config["token_budget"], config["tool_budget"]))

    def reserve(self, run, tokens=0, tools=0):
        if tokens < 0 or tools < 0:
            raise ValueError("Reservations cannot be negative.")
        with self.db(True) as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
            if row is None or row["reserved_tokens"]+tokens > row["token_limit"] or row["tool_calls"]+tools > row["tool_limit"]:
                raise WorkflowError("budget_exhausted", "The saved workflow budget is exhausted. No call was made.")
            # Deliberately no refunds: failed/crashed calls still consume their
            # reservation. This stays conservative across process restarts.
            db.execute("UPDATE runs SET reserved_tokens=reserved_tokens+?,tool_calls=tool_calls+? WHERE id=?", (tokens, tools, run))

    def event(self, run, kind, role, details):
        # Callers pass metadata/hashes only. Never raw prompts, tool arguments,
        # credentials, model reasoning or approval capabilities.
        with self.db(True) as db:
            db.execute("INSERT INTO events(run,kind,role,details,at) VALUES(?,?,?,?,?)", (run, kind, role, canonical(details), time.time()))

    def extend_development_budget(self, run, *, expected_limit, new_limit):
        """Explicit operator recovery; never exposed as a model tool or refunded."""
        if (type(expected_limit) is not int or type(new_limit) is not int
                or not 1000 <= expected_limit < new_limit <= 2000000):
            raise WorkflowError("invalid_budget_extension", "Use a higher total token limit, at most 2000000.")
        with self.db(True) as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
            if row is None or row["token_limit"] != expected_limit:
                raise WorkflowError("stale_budget", "The saved token limit changed or the run does not exist. Inspect status before confirming again.")
            details = {"previous_token_limit": expected_limit, "new_token_limit": new_limit,
                       "reserved_tokens": row["reserved_tokens"], "tool_calls": row["tool_calls"],
                       "tool_limit": row["tool_limit"], "reason": "operator_development_recovery",
                       "fixed_budget_evaluation_eligible": False}
            db.execute("UPDATE runs SET token_limit=? WHERE id=?", (new_limit, run))
            db.execute("INSERT INTO events(run,kind,role,details,at) VALUES(?,?,?,?,?)",
                       (run, "development_budget_extended", "human", canonical(details), time.time()))
        return details

    def save_evidence(self, run, key, value):
        with self.db(True) as db:
            old = db.execute("SELECT content FROM evidence WHERE run=? AND id=?", (run, key)).fetchone()
            if old and old[0] != canonical(value):
                raise WorkflowError("changed_evidence", "Previously loaded evidence changed during this run.")
            db.execute("INSERT OR IGNORE INTO evidence VALUES(?,?,?)", (run, key, canonical(value)))

    def evidence(self, run, keys=None):
        import json
        with self.db() as db:
            records = {row["id"]: json.loads(row["content"]) for row in db.execute("SELECT * FROM evidence WHERE run=? ORDER BY id", (run,))}
        if keys is None:
            return records
        if set(keys)-records.keys():
            raise WorkflowError("unknown_evidence", "A handoff or draft refers to evidence that was never loaded.")
        return {key: records[key] for key in keys}

    def cached(self, run, key, value=None):
        import json
        with self.db(value is not None) as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO responses VALUES(?,?,?)", (run, key, canonical(value)))
                return value
            row = db.execute("SELECT content FROM responses WHERE run=? AND id=?", (run, key)).fetchone()
            return json.loads(row[0]) if row else None

    def report(self, run):
        import json
        from decimal import Decimal
        with self.db() as db:
            row = dict(db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone())
            events = [dict(r) for r in db.execute("SELECT seq,kind,role,details,at FROM events WHERE run=? ORDER BY seq", (run,))]
        for event in events:
            event["details"] = json.loads(event["details"])
        completed=[e["details"]["usage"] for e in events if e["kind"]=="llm_end"]
        started=sum(e["kind"]=="llm_start" for e in events)
        cost_complete=len(completed)==started and all(u.get("cost") is not None for u in completed)
        metrics={"llm_requests_started":started,"llm_responses_received":len(completed),
                 "reported_total_tokens":sum(u["total_tokens"] for u in completed),
                 "reported_cost_usd":str(sum((Decimal(str(u["cost"])) for u in completed),Decimal(0))) if cost_complete else None,
                 "cost_complete":cost_complete,"critic_rejections":sum(e["kind"]=="critic_rejected" for e in events),
                 "model_response_cache_hits":sum(e["kind"]=="llm_cache_hit" for e in events)}
        extensions = sum(e["kind"] == "development_budget_extended" for e in events)
        metrics.update(development_budget_extensions=extensions,
                       budget_protocol="operator_extended_development" if extensions else "original_fixed_limit")
        return {"budget": row, "metrics":metrics, "events": events}
