import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dotenv import dotenv_values
from research_tools_client import local_url
from review_portal_note import safe_display
from setup_research_tools import KEYS, setup


class ResearchToolsSetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root/".git/info").mkdir(parents=True)
        (self.root/".env").write_text("EXISTING_VALUE=keep_this\n")

    def test_setup_preserves_existing_values_and_is_idempotent(self):
        setup(self.root)
        first = (self.root/".env").read_bytes()
        setup(self.root)
        self.assertEqual(first, (self.root/".env").read_bytes())
        values = dotenv_values(self.root/".env")
        self.assertEqual(values["EXISTING_VALUE"], "keep_this")
        self.assertEqual(len({values[k] for k in KEYS}), 5)
        self.assertEqual((self.root/".env").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root/"data/research_portal").stat().st_mode & 0o777, 0o700)
        self.assertIn("/data/research_portal/", (self.root/".git/info/exclude").read_text())

    def test_invalid_or_shared_existing_tokens_not_overwritten(self):
        for raw in ["SANDBOX_API_TOKEN=short\n", "PORTAL_READ_TOKEN="+"x"*40+"\nPORTAL_DRAFT_TOKEN="+"x"*40+"\n"]:
            (self.root/".env").write_text(raw)
            with self.assertRaises(ValueError):
                setup(self.root)
            self.assertEqual((self.root/".env").read_text(), raw)

    def test_env_symlink_rejected(self):
        (self.root/".env").rename(self.root/"outside")
        (self.root/".env").symlink_to(self.root/"outside")
        with self.assertRaises(ValueError):
            setup(self.root)

    def test_operator_credentials_cannot_be_sent_to_remote_url(self):
        for url in ["https://attacker.invalid", "http://127.0.0.1@attacker.invalid", "http://127.0.0.1:8004/?secret=x", "http://127.0.0.1:8004/path"]:
            with self.assertRaises(ValueError):
                local_url(url)
        self.assertEqual(local_url("http://127.0.0.1:8004/"), "http://127.0.0.1:8004")

    def test_terminal_escape_sequences_in_notes_are_neutralized(self):
        text = safe_display("Hello\x1b[2J\u202eevil\n")
        self.assertNotIn("\x1b", text); self.assertNotIn("\u202e", text)
        self.assertIn("\\u001b", text)


if __name__ == "__main__":
    unittest.main()
