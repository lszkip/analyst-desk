"""Owner-scoped, local LangGraph memory; remembered notes are not fresh evidence.

Only the trusted application binds ``owner``. Never expose this store or its CRUD
methods as model tools. Private watchlist values are excluded from all context
and checked again at external model/tool dispatch. This is not a network DLP or
protection against an attacker who can read the local account's database.
"""
from contextlib import contextmanager
from pathlib import Path
import json
import re
import threading
import unicodedata
from urllib.parse import quote

from langgraph.store.base import BaseStore
from langgraph.store.sqlite import SqliteStore

from workflow_contracts import WorkflowError, canonical, digest

MEMORY_VERSION = "analyst-memory-v1"
MAX_ITEMS = 500
PREFERENCE_VALUES = {
    "detail_level": {"concise", "standard", "detailed"},
    "response_format": {"table", "prose", "both"},
    "risk_focus": {"competition", "supplier", "cyber", "all"},
}


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise WorkflowError("memory_key", "Memory identifiers must be bounded plain identifiers.")
    return value


def _normalise(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold()
                   if unicodedata.category(c) != "Cf")


def _strings(value, decoded_layers=0):
    """Inspect native strings and bounded JSON transport envelopes unchanged.

    Agent message content is already serialized JSON when the pre-dispatch
    guard receives it. Decode only comparison copies, at most three envelopes;
    otherwise quotation marks and newlines can hide a literal private value.
    This does not decode arbitrary encodings or rewrite the outgoing payload.
    """
    if isinstance(value, str):
        yield value
        if decoded_layers < 3 and value.lstrip().startswith(("{", "[", '"')):
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                return
            if decoded != value:
                yield from _strings(decoded, decoded_layers+1)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item, decoded_layers)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item, decoded_layers)


class OwnerStore:
    """A real LangGraph SQLite BaseStore with owner namespaces and bounded rows.

    This is logical account isolation in the trusted application, not an auth
    service. The caller must bind owner from its authenticated local session.
    """
    def __init__(self, path, owner):
        if not isinstance(owner, str) or not 1 <= len(owner.strip()) <= 200:
            raise WorkflowError("memory_owner", "A trusted local owner identity is required.")
        self.owner_scope = digest(owner)
        self.path = Path(path)
        if self.path.is_symlink() or any(p.is_symlink() for p in self.path.parents):
            raise WorkflowError("memory_path", "Memory storage must not traverse a symlink.")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        with self.db() as store:
            assert isinstance(store, BaseStore)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        with self._lock, SqliteStore.from_conn_string(str(self.path)) as store:
            store.setup()
            yield store

    def namespace(self, kind):
        return ("analyst", self.owner_scope, _key(kind))

    def get(self, kind, key):
        with self.db() as store:
            row = store.get(self.namespace(kind), _key(key))
            return row.value if row else None

    def rows(self, kind):
        with self.db() as store:
            return [{"key": row.key, **row.value} for row in store.search(self.namespace(kind), limit=MAX_ITEMS)]

    def put(self, kind, key, value, expected_hash=None):
        key = _key(key)
        if len(canonical(value).encode()) > 80000:
            raise WorkflowError("memory_size", "Memory entry is too large.")
        with self._lock, self.db() as store:
            old = store.get(self.namespace(kind), key)
            if old:
                if expected_hash != old.value["content_sha256"]:
                    raise WorkflowError("memory_stale", "Review the current memory content hash before correcting it.")
            elif expected_hash is not None:
                raise WorkflowError("memory_stale", "The memory entry no longer exists.")
            if old is None and len(store.search(self.namespace(kind), limit=MAX_ITEMS)) >= MAX_ITEMS:
                raise WorkflowError("memory_capacity", "Delete an old memory entry before adding more.")
            envelope = {"value": value, "content_sha256": digest(value)}
            store.put(self.namespace(kind), key, envelope, index=False)
            return {"key": key, **envelope}

    def delete(self, kind, key, expected_hash):
        with self._lock, self.db() as store:
            old = store.get(self.namespace(kind), _key(key))
            if old is None or expected_hash != old.value["content_sha256"]:
                raise WorkflowError("memory_stale", "Review the current memory content hash before deletion.")
            store.delete(self.namespace(kind), key)
        return {"deleted": True, "key": key}


class AnalystMemory:
    def __init__(self, path, owner, enabled=True):
        self.storage = OwnerStore(path, owner)
        self.enabled = bool(enabled)

    def set_preference(self, key, value, expected_hash=None):
        if key not in PREFERENCE_VALUES or not isinstance(value, str) or value not in PREFERENCE_VALUES[key]:
            raise WorkflowError("memory_preference", "Use an allowlisted analyst style preference.")
        return self.storage.put("preferences", key, value, expected_hash)

    def set_watchlist(self, key, label, note="", expected_hash=None):
        if (not isinstance(label, str) or not 2 <= len(label.strip()) <= 160
                or not isinstance(note, str) or len(note) > 1000):
            raise WorkflowError("watchlist_value", "Use a bounded private label and optional local note.")
        return self.storage.put("watchlist", key, {"label": label.strip(), "note": note}, expected_hash)

    def read(self, kind, key=None):
        if kind not in {"preferences", "watchlist", "coverage", "published_notes"}:
            raise WorkflowError("memory_kind", "Unknown memory category.")
        return self.storage.get(kind, key) if key else self.storage.rows(kind)

    def delete(self, kind, key, expected_hash):
        if kind not in {"preferences", "watchlist", "coverage", "published_notes"}:
            raise WorkflowError("memory_kind", "Unknown memory category.")
        result = self.storage.delete(kind, key, expected_hash)
        if kind == "published_notes":
            coverage = self.storage.get("coverage", key)
            if coverage:
                self.storage.delete("coverage", key, coverage["content_sha256"])
        return result

    def guard_external(self, payload, approved_tickers=()):
        """Negative check before BOTH model and MCP/tool dispatch, even when off.

        No private value is included in errors. Literal normalized labels/notes
        are blocked; encoding/semantic paraphrases are outside this check's scope.
        A label that is exactly a plain ticker may appear only if that ticker is
        explicitly in the trusted reviewed request scope. This does not export
        list membership or private notes. Never derive this scope from a model.
        """
        outgoing = {_normalise(canonical(payload)), *(_normalise(text) for text in _strings(payload))}
        public_tickers = {t for t in approved_tickers if isinstance(t, str) and re.fullmatch(r"[A-Z]{1,6}", t)}
        for row in self.storage.rows("watchlist"):
            private = row["value"]
            for field, secret in private.items():
                if field == "label" and secret in public_tickers:
                    continue
                if secret.strip() and any(_normalise(secret.strip()) in text for text in outgoing):
                    raise WorkflowError("private_memory_exfiltration", "An external payload contains a local-only private memory value. No dispatch is allowed.")
        return payload

    def record_published(self, state, records, run_id):
        """Trusted post-publication hook; stores only exact checked final notes.

        Rechecks local receipts/rendering. It does not contact the portal or make
        a historical note independently true; the caller supplies the confirmed
        publication result from the actual publication path.
        """
        from research_portal import ResearchNote, note_hash
        from workflow_render import render_note
        from workflow_support import require_current_support, validate_support
        if not self.enabled:
            return {"stored": False, "reason": "memory_disabled"}
        publication = state.get("publication", {})
        if state.get("status") != "published" or not publication.get("note_id"):
            raise WorkflowError("memory_unpublished", "Only a confirmed published note can enter past-note memory.")
        note = ResearchNote.model_validate(state["note"])
        expected = note_hash(note)
        if publication.get("content_hash") != expected or state.get("approval_hash") != expected:
            raise WorkflowError("memory_unverified", "The published note must match its exact approved content hash.")
        require_current_support(state, records)
        reviews = state["critique"].get("paragraph_reviews", [])
        if len(reviews) != len(state["draft"]["paragraphs"]):
            raise WorkflowError("memory_unverified", "Current sentence receipts are required for every paragraph.")
        for paragraph, review in zip(state["draft"]["paragraphs"], reviews):
            if validate_support(review, paragraph, records):
                raise WorkflowError("memory_unverified", "A saved paragraph does not have valid current source receipts.")
        rendered, verification = render_note(state["draft"], records, state["request"])
        if note_hash(rendered) != expected:
            raise WorkflowError("memory_unverified", "Current evidence does not reproduce the saved published note.")
        self.guard_external(note.model_dump(), state["request"]["tickers"])
        key = _key(publication["note_id"])
        value = {"version": MEMORY_VERSION, "note_id": key, "run_id": _key(run_id),
                 "content_hash": expected, "request": state["request"], "note": note.model_dump(),
                 "verification": verification, "support_version": state["critique"]["check_version"],
                 "memory_status": "previously_published_verified_snapshot_not_current_truth"}
        old = self.storage.get("published_notes", key)
        if old:
            if old["value"] != value:
                raise WorkflowError("memory_changed_note", "Published note snapshots are immutable; delete or store a newly published correction.")
            return {"stored": True, "note_id": key, "content_sha256": old["content_sha256"], "already_stored": True}
        saved = self.storage.put("published_notes", key, value)
        self.storage.put("coverage", key, {"note_id": key, "tickers": note.tickers,
            "years": note.fiscal_years, "content_hash": expected,
            "scope": "previous_note_request_only_not_complete_filing_coverage"})
        return {"stored": True, "note_id": key, "content_sha256": saved["content_sha256"], "already_stored": False}

    def _relevant_notes(self, request):
        tickers = set(request.get("tickers", []))
        # SQLite updates are ordered newest first; note records are immutable.
        return [row["value"] for row in self.storage.rows("published_notes")
                if tickers.intersection(row["value"]["note"]["tickers"])]

    @staticmethod
    def _summary(row):
        note = row["note"]
        return {"note_id": row["note_id"], "note_link": "/notes/"+quote(row["note_id"], safe=""),
                "content_hash": row["content_hash"], "title": note["title"],
                "tickers": note["tickers"], "fiscal_years": note["fiscal_years"],
                "sources": note["citations"], "status": row["memory_status"]}

    def context(self, request, max_chars=6000):
        if not 256 <= max_chars <= 12000:
            raise WorkflowError("memory_context_bound", "Memory context must be bounded between 256 and 12000 characters.")
        base = {"enabled": self.enabled, "status": "Memory is prior context, not evidence or new facts.",
                "preferences": {}, "past_notes": [], "truncated": False}
        if not self.enabled:
            return base
        base["preferences"] = {row["key"]: row["value"] for row in self.storage.rows("preferences")}
        for row in self._relevant_notes(request):
            summary = self._summary(row)
            candidate = {**base, "past_notes": [*base["past_notes"], summary]}
            if len(canonical(candidate)) > max_chars:
                base["truncated"] = True
                break
            base = candidate
            if len(base["past_notes"]) >= 3:
                base["truncated"] = len(self._relevant_notes(request)) > 3
                break
        # A private value accidentally copied into a prior public note must not
        # leak merely because it already exists in a stored snapshot.
        self.guard_external(base, request.get("tickers", ()))
        if len(canonical(base)) > max_chars:
            base["preferences"] = {}
            base["truncated"] = True
        return base

    def changes_since_last_note(self, request, current_sources):
        """Deterministic source-inventory delta, never an inferred business change.

        ``current_sources`` is a list of current Citation-shaped dictionaries;
        callers must retrieve and verify those sources in the current run.
        """
        from research_portal import Citation
        current = [Citation.model_validate(row).model_dump() for row in current_sources]
        notes = self._relevant_notes(request) if self.enabled else []
        if not notes:
            return {"status": "no_prior_note", "changes": [], "semantic_change_assessed": False}
        previous = notes[0]
        old = {row["evidence_id"]: row for row in previous["note"]["citations"]}
        new = {row["evidence_id"]: row for row in current}
        changes = []
        for key in sorted(old.keys() | new.keys()):
            if key not in old:
                changes.append({"change": "new_source_in_current_inventory", "current": new[key]})
            elif key not in new:
                changes.append({"change": "previous_source_not_in_current_inventory", "previous": old[key]})
            elif old[key] != new[key]:
                changes.append({"change": "source_record_changed_requires_reverification", "previous": old[key], "current": new[key]})
        result = {"status": "source_inventory_comparison", "previous_note": self._summary(previous),
                  "changes": changes[:100], "truncated": len(changes)>100, "semantic_change_assessed": False,
                  "limitation": "Source-list differences do not establish that a business risk or filing fact changed. Retrieve and check current sources."}
        self.guard_external(result, request.get("tickers", ()))
        return result


def deterministic_memory_comparison(memory, request, current_sources):
    """Report hook for an offline controlled comparison, with no model claims."""
    original = memory.enabled
    try:
        rows = []
        for enabled in (False, True):
            memory.enabled = enabled
            context = memory.context(request)
            followup = memory.changes_since_last_note(request, current_sources)
            rows.append({"memory_enabled": enabled, "context": context, "followup": followup,
                         "context_characters": len(canonical(context)), "model_calls": 0})
        return {"comparison_type": "deterministic_offline_memory_on_off", "live_model_quality_comparison": False,
                "request_sha256": digest(request), "current_sources_sha256": digest(current_sources), "runs": rows}
    finally:
        memory.enabled = original
