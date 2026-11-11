"""Fictional isolated portal fixtures; approval here is simulated test input."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError
from research_portal import PortalStore, ResearchNote, ReviewDecision
from tool_security import ToolError


def fixture_note(title="Fictional test note"):
    return ResearchNote(title=title, body_markdown="Synthetic integration fixture. No investment recommendation.",
        tickers=["FICT"], fiscal_years=[2025], citations=[{"evidence_id": "fixture_cell_1", "source_sha256": "a"*64,
        "source_url": "https://www.sec.gov/Archives/edgar/data/0/fixture.htm"}])


class PortalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "portal.sqlite3"
        self.now = 1000
        self.store = PortalStore(self.path, clock=lambda: self.now)
        self.note = fixture_note()
        self.proposal = self.store.prepare(self.note, "fixture_1")
        self.pid, self.digest = self.proposal["proposal_id"], self.proposal["content_hash"]

    def review(self, action="approve", digest=None, note=None):
        return self.store.review(self.pid, ReviewDecision(expected_hash=digest or self.digest,
            action=action, reviewer="Synthetic test reviewer", edited_note=note))

    def test_prepare_is_idempotent_and_not_published(self):
        self.assertEqual(self.store.prepare(self.note, "fixture_1"), self.proposal)
        self.assertEqual(self.store.list_notes()["total"], 0)

    def test_idempotency_key_cannot_replace_content(self):
        with self.assertRaises(ToolError):
            self.store.prepare(fixture_note("Changed"), "fixture_1")
        self.assertEqual(self.store.get_proposal(self.pid)["content_hash"], self.digest)

    def test_publish_requires_approval(self):
        with self.assertRaises(ToolError):
            self.store.publish(self.pid, self.digest, "x"*43)
        self.assertEqual(self.store.list_notes()["total"], 0)

    def test_published_content_is_exact_and_retries_do_not_duplicate(self):
        approval = self.review()
        args = (self.pid, self.digest, approval["approval_token"])
        first = self.store.publish(*args)
        self.assertEqual(first["note"], self.note.model_dump())
        self.assertEqual(first, self.store.publish(*args))
        self.assertEqual(self.store.get_note(first["note_id"]), first)
        self.assertEqual(self.store.list_notes()["total"], 1)

    def test_published_proposal_links_to_exact_saved_note_after_restart(self):
        approval = self.review()
        note = self.store.publish(self.pid, self.digest, approval["approval_token"])
        proposal = PortalStore(self.path).get_proposal(self.pid)
        self.assertEqual(proposal["state"], "published")
        self.assertEqual(proposal["note_id"], note["note_id"])
        self.assertEqual(proposal["content_hash"], note["content_hash"])
        self.assertEqual(proposal["published_at"], note["published_at"])
        self.assertNotIn(approval["approval_token"], str(proposal))

    def test_missing_or_inconsistent_published_note_cannot_report_success(self):
        approval = self.review()
        self.store.publish(self.pid, self.digest, approval["approval_token"])
        with self.store.connect(write=True) as db:
            db.execute("UPDATE notes SET content_hash=? WHERE proposal_id=?", ("b"*64, self.pid))
        with self.assertRaises(ToolError) as exc:
            self.store.get_proposal(self.pid)
        self.assertEqual(exc.exception.code, "inconsistent_publication")
        with self.store.connect(write=True) as db:
            db.execute("DELETE FROM notes WHERE proposal_id=?", (self.pid,))
        with self.assertRaises(ToolError):
            self.store.get_proposal(self.pid)

    def test_approval_is_not_exposed_by_read_tools(self):
        approval = self.review()
        self.assertNotIn(approval["approval_token"], str(self.store.get_proposal(self.pid)))
        self.assertNotIn("approval_hash", self.store.get_proposal(self.pid))

    def test_edit_revokes_previous_approval_and_requires_new_review(self):
        old = self.review()
        edited = self.review("edit", note=fixture_note("Edited"))
        self.assertEqual(edited["revision"], 2); self.assertEqual(edited["state"], "pending")
        for digest in (self.digest, edited["content_hash"]):
            with self.assertRaises(ToolError):
                self.store.publish(self.pid, digest, old["approval_token"])
        fresh = self.review(digest=edited["content_hash"])
        result = self.store.publish(self.pid, fresh["content_hash"], fresh["approval_token"])
        self.assertEqual(result["note"]["title"], "Edited")

    def test_stale_review_rejected(self):
        self.review("edit", note=fixture_note("Changed"))
        with self.assertRaises(ToolError):
            self.review()

    def test_rejected_note_cannot_be_approved_or_published(self):
        approval = self.review()
        self.review("reject")
        with self.assertRaises(ToolError):
            self.review()
        with self.assertRaises(ToolError):
            self.store.publish(self.pid, self.digest, approval["approval_token"])

    def test_expired_approval_rejected(self):
        approval = self.review(); self.now += 601
        with self.assertRaises(ToolError):
            self.store.publish(self.pid, self.digest, approval["approval_token"])
        self.assertTrue(self.store.get_proposal(self.pid)["approval_expired"])

    def test_wrong_hash_wrong_token_and_cross_note_token_rejected(self):
        approval = self.review()
        another = self.store.prepare(fixture_note("Other"), "fixture_2")
        for args in [(self.pid, "b"*64, approval["approval_token"]), (self.pid, self.digest, "x"*43),
                     (another["proposal_id"], another["content_hash"], approval["approval_token"])]:
            with self.assertRaises(ToolError):
                self.store.publish(*args)

    def test_concurrent_publish_creates_one_note(self):
        approval = self.review()
        def publish(_):
            return self.store.publish(self.pid, self.digest, approval["approval_token"])["note_id"]
        with ThreadPoolExecutor(max_workers=6) as pool:
            ids = list(pool.map(publish, range(6)))
        self.assertEqual(len(set(ids)), 1); self.assertEqual(self.store.list_notes()["total"], 1)

    def test_restart_preserves_approval_and_notes(self):
        approval = self.review()
        restarted = PortalStore(self.path, clock=lambda: self.now)
        note = restarted.publish(self.pid, self.digest, approval["approval_token"])
        self.assertEqual(PortalStore(self.path).get_note(note["note_id"]), note)

    def test_published_proposal_is_immutable(self):
        approval = self.review(); self.store.publish(self.pid, self.digest, approval["approval_token"])
        for action, note in [("edit", fixture_note("Changed")), ("reject", None), ("approve", None)]:
            with self.assertRaises(ToolError):
                self.review(action, note=note)

    def test_audit_records_state_transitions_without_credentials(self):
        approval = self.review(); self.store.publish(self.pid, self.digest, approval["approval_token"])
        with self.store.connect() as db:
            events = [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY seq")]
        self.assertEqual([e["action"] for e in events], ["prepared", "approve", "published"])
        self.assertNotIn(approval["approval_token"], str(events))

    def test_invalid_citations_and_undeclared_fields_rejected(self):
        data = self.note.model_dump()
        for url in ["http://www.sec.gov/Archives/edgar/data/1/f.htm", "https://attacker.invalid/?watchlist=secret",
                    "https://www.sec.gov/Archives/edgar/data/1/f.htm?secret=x"]:
            data["citations"][0]["source_url"] = url
            with self.assertRaises(ValidationError):
                ResearchNote.model_validate(data)
        with self.assertRaises(ValidationError):
            ResearchNote.model_validate({**self.note.model_dump(), "approved": True})

    def test_complete_note_byte_limit(self):
        with self.assertRaises(ValidationError):
            ResearchNote.model_validate({**self.note.model_dump(), "body_markdown": "界"*30000})


if __name__ == "__main__":
    unittest.main()
