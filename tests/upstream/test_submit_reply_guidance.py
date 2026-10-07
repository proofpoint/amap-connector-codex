"""tests/test_submit_reply_guidance.py — inbox-submit tells the agent it is how
a delegation is answered.

An observed failure: an agent received a delegation, replied with Claude Code's
SendMessage (which the harness's own note on cross-session messages suggests),
failed because SendMessage cannot reach a peer address, and concluded the peer
was unreachable. inbox-submit's text was the one place present in every
session that could have corrected it, and it described itself only as mail.
These tests pin that the correction stays in both places the agent reads.
Stdlib only.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
_ro = SourceFileLoader("_ro_for_reply_guidance", str(HERE / "test_readonly_inbound.py")).load_module()
_McpProc = _ro._McpProc


class SubmitReplyGuidance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(tempfile.mkdtemp(prefix="reply-guidance-"))
        (tmp / "outbox").mkdir()
        env = {k: v for k, v in os.environ.items() if k != "MAILBOX_ROOT_DIR"}
        env["OUTBOX_DIR"] = str(tmp / "outbox")
        proc = _McpProc("inbox-submit", env, extra_args=["mcp"])
        try:
            cls.instructions = proc.initialize()["result"]["instructions"]
            cls.tools = {t["name"]: t for t in proc.request("tools/list")["result"]["tools"]}
        finally:
            proc.close()

    def test_the_server_instructions_use_codex_peer_reply_metadata(self):
        self.assertIn("ANSWER A PEER DELEGATION", self.instructions)
        self.assertIn("peer_from", self.instructions)
        self.assertIn("peer_message_id", self.instructions)
        self.assertIn("notice_id", self.instructions)
        self.assertNotIn("Claude", self.instructions)

    def test_the_submit_description_says_it_too(self):
        desc = self.tools["submit"]["description"]
        self.assertIn("ANSWER A PEER DELEGATION", desc)
        self.assertIn("peer_from", desc)
        self.assertIn("peer_message_id", desc)
        self.assertIn("notice_id", desc)

    def test_the_in_reply_to_field_says_where_the_id_comes_from(self):
        field = self.tools["submit"]["inputSchema"]["properties"]["in_reply_to"]["description"]
        self.assertIn("peer_message_id", field)
        self.assertIn("not its local notice_id", field)


if __name__ == "__main__":
    unittest.main()
