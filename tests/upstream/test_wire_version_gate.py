"""Codex port compatibility gate for inbound wire majors."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_readonly_inbound import _BASE_ENV, _McpProc


class WireVersionGateTest(unittest.TestCase):
    def test_read_message_requires_wire_major_two(self):
        for version, accepted in (("2", True), ("2.3.0", True), (None, False),
                                  ("1", False), ("3", False), ("v2", False)):
            with self.subTest(contract_version=version):
                with tempfile.TemporaryDirectory(prefix="amap-version-") as temp:
                    messages = Path(temp)
                    doc = {
                        "notice_id": "version-test-1",
                        "body_text": "body",
                        "from": "a@example.org",
                    }
                    if version is not None:
                        doc["contract_version"] = version
                    (messages / "notice-version-test-1.json").write_text(json.dumps(doc))
                    env = {**_BASE_ENV, "INBOX_MESSAGE_DIR": str(messages)}
                    proc = _McpProc("inbox-mcp-vol", env)
                    try:
                        proc.initialize()
                        result = proc.request(
                            "tools/call",
                            {"name": "read_message", "arguments": {"id": "version-test-1"}},
                        )["result"]
                        self.assertEqual("isError" not in result, accepted)
                        if not accepted:
                            self.assertIn("contract_version", result["content"][0]["text"])
                    finally:
                        proc.close()


if __name__ == "__main__":
    unittest.main()
