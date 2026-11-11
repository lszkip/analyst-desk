"""Persistent mock research portal with exact-content approval and atomic publication."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tool_security import ToolError, credential_digest

SHA = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
IDENTIFIER = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")]


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    evidence_id: Annotated[str, Field(min_length=1, max_length=160)]
    source_sha256: SHA
    source_url: Annotated[str, Field(max_length=1000)]

    @field_validator("source_url")
    @classmethod
    def source(cls, value):
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.netloc not in {"www.sec.gov", "sec.gov"} or not parts.path.startswith("/Archives/edgar/data/") or parts.query or parts.fragment:
            raise ValueError("Citations must use an SEC archive HTTPS URL without query parameters.")
        return value


class ResearchNote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: Annotated[str, Field(min_length=1, max_length=200)]
    body_markdown: Annotated[str, Field(min_length=1, max_length=50000)]
    tickers: Annotated[list[Annotated[str, Field(pattern=r"^[A-Z]{1,6}$")]], Field(min_length=1, max_length=15)]
    fiscal_years: Annotated[list[Annotated[int, Field(ge=2000, le=2100)]], Field(min_length=1, max_length=10)]
    citations: Annotated[list[Citation], Field(min_length=1, max_length=100)]

    @field_validator("title", "body_markdown")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Text cannot be blank.")
        return value

    @model_validator(mode="after")
    def total_size(self):
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > 65536:
            raise ValueError("The complete note must fit within 65536 UTF-8 bytes.")
        return self


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_hash: SHA
    action: Literal["approve", "edit", "reject"]
    reviewer: Annotated[str, Field(min_length=1, max_length=100)]
    edited_note: ResearchNote | None = None


def canonical(note):
    return json.dumps(note.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def note_hash(note):
    return hashlib.sha256(canonical(note).encode()).hexdigest()


class PortalStore:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        if self.path.is_symlink():
            raise ValueError("Portal database cannot be a symlink.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                    initial_hash TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL,
                    approval_hash TEXT, approval_expires REAL, reviewer TEXT);
                CREATE TABLE IF NOT EXISTS notes (
                    id TEXT PRIMARY KEY, proposal_id TEXT UNIQUE NOT NULL,
                    content TEXT NOT NULL, content_hash TEXT NOT NULL, revision INTEGER NOT NULL,
                    reviewer TEXT NOT NULL, published_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL,
                    action TEXT NOT NULL, content_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL, actor TEXT NOT NULL, occurred_at REAL NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
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

    def _row(self, db, proposal_id):
        row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise ToolError("not_found", "No such proposal.")
        return row

    def _public(self, row):
        return {"proposal_id": row["id"], "note": json.loads(row["content"]), "content_hash": row["content_hash"],
                "revision": row["revision"], "state": row["state"], "created_at": row["created_at"],
                "approval_expired": bool(row["state"] == "approved" and row["approval_expires"] <= self.clock()),
                "untrusted_content": True}

    def _audit(self, db, row, action, actor):
        db.execute("INSERT INTO audit(proposal_id,action,content_hash,revision,actor,occurred_at) VALUES(?,?,?,?,?,?)",
                   (row["id"], action, row["content_hash"], row["revision"], actor, self.clock()))

    def prepare(self, note, idempotency_key):
        content, digest = canonical(note), note_hash(note)
        with self.connect(write=True) as db:
            row = db.execute("SELECT * FROM proposals WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if row is not None:
                if row["initial_hash"] != digest:
                    raise ToolError("idempotency_conflict", "This idempotency key was used for different content.")
                return self._public(row)
            pid = "proposal_" + secrets.token_hex(16)
            db.execute("INSERT INTO proposals(id,idempotency_key,initial_hash,content,content_hash,revision,state,created_at) VALUES(?,?,?,?,?,1,'pending',?)",
                       (pid, idempotency_key, digest, content, digest, self.clock()))
            row = self._row(db, pid)
            self._audit(db, row, "prepared", "draft-client")
            return self._public(row)

    def get_proposal(self, proposal_id):
        with self.connect() as db:
            row = self._row(db, proposal_id)
            result = self._public(row)
            if row["state"] == "published":
                published = db.execute(
                    "SELECT id,content_hash,revision,published_at FROM notes WHERE proposal_id=?",
                    (proposal_id,),
                ).fetchone()
                if published is None or published["content_hash"] != row["content_hash"] or published["revision"] != row["revision"]:
                    raise ToolError("inconsistent_publication", "The saved publication does not match this proposal.")
                result["note_id"] = published["id"]
                result["published_at"] = published["published_at"]
            return result

    def review(self, proposal_id, decision):
        if (decision.action == "edit") != (decision.edited_note is not None):
            raise ToolError("invalid_decision", "Only an edit must include an edited note.")
        with self.connect(write=True) as db:
            row = self._row(db, proposal_id)
            if row["content_hash"] != decision.expected_hash:
                raise ToolError("stale_review", "The note changed. Read its current content before reviewing.")
            if row["state"] in {"published", "rejected"}:
                raise ToolError("closed_proposal", "This proposal has already been published or rejected.")
            token = None
            if decision.action == "edit":
                db.execute("UPDATE proposals SET content=?,content_hash=?,revision=revision+1,state='pending',approval_hash=NULL,approval_expires=NULL,reviewer=NULL WHERE id=?",
                           (canonical(decision.edited_note), note_hash(decision.edited_note), proposal_id))
            elif decision.action == "reject":
                db.execute("UPDATE proposals SET state='rejected',approval_hash=NULL,approval_expires=NULL,reviewer=? WHERE id=?", (decision.reviewer, proposal_id))
            else:
                token = secrets.token_urlsafe(32)
                db.execute("UPDATE proposals SET state='approved',approval_hash=?,approval_expires=?,reviewer=? WHERE id=?",
                           (credential_digest(token), self.clock() + 600, decision.reviewer, proposal_id))
            row = self._row(db, proposal_id)
            self._audit(db, row, decision.action, decision.reviewer)
            result = self._public(row)
            if token is not None:
                result["approval_token"] = token
                result["approval_expires"] = row["approval_expires"]
            return result

    def publish(self, proposal_id, expected_hash, approval_token):
        with self.connect(write=True) as db:
            row = self._row(db, proposal_id)
            if row["content_hash"] != expected_hash or not row["approval_hash"] or not secrets.compare_digest(row["approval_hash"], credential_digest(approval_token)):
                raise ToolError("approval_required", "A human approval for this exact content is required.")
            existing = db.execute("SELECT * FROM notes WHERE proposal_id=?", (proposal_id,)).fetchone()
            if row["state"] == "published" and existing is not None:
                return self._note(existing)
            if row["state"] != "approved" or row["approval_expires"] <= self.clock():
                raise ToolError("approval_required", "Approval is missing, revoked or expired.")
            note_id = "note_" + secrets.token_hex(16)
            db.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?)", (note_id, proposal_id, row["content"], row["content_hash"], row["revision"], row["reviewer"], self.clock()))
            db.execute("UPDATE proposals SET state='published' WHERE id=?", (proposal_id,))
            self._audit(db, row, "published", "publisher-client")
            return self._note(db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone())

    def _note(self, row):
        return {"note_id": row["id"], "proposal_id": row["proposal_id"], "note": json.loads(row["content"]),
                "content_hash": row["content_hash"], "revision": row["revision"], "reviewer": row["reviewer"],
                "published_at": row["published_at"], "untrusted_content": True}

    def get_note(self, note_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
            if row is None:
                raise ToolError("not_found", "No such published note.")
            return self._note(row)

    def list_notes(self, offset=0, limit=10):
        with self.connect() as db:
            rows = db.execute("SELECT id,content,content_hash,published_at FROM notes ORDER BY published_at DESC,id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            total = db.execute("SELECT count(*) FROM notes").fetchone()[0]
        return {"notes": [{"note_id": r["id"], "title": json.loads(r["content"])["title"], "content_hash": r["content_hash"], "published_at": r["published_at"], "untrusted_content": True} for r in rows],
                "total": total, "next_offset": offset+limit if offset+limit < total else None}

    def health(self):
        with self.connect() as db:
            db.execute("SELECT 1 FROM notes LIMIT 1").fetchall()
        return {"status": "ok", "service": "research-portal"}
