"""Check adapter envelopes against the installed build's generated contract."""
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path

import jsonschema

from amap_codex.app_server import AppServerClient, AppServerDelivery
from amap_codex.config import Config

ROOT = Path(__file__).resolve().parents[1]


def schema(name):
    return json.loads((ROOT / "compatibility/schema" / f"{name}.json").read_text())


def test_generated_schema_manifest():
    manifest = json.loads((ROOT / "compatibility/schema-manifest.json").read_text())
    for name, expected in manifest["sha256"].items():
        assert hashlib.sha256((ROOT / "compatibility/schema" / name).read_bytes()).hexdigest() == expected


def test_no_model_leaves_the_choice_to_codex():
    """A configuration that names no model sends none, so the app-server uses
    its own default, as the agent's pane does."""
    delivery = AppServerDelivery(AppServerClient(["codex", "app-server"]), model=None, cwd="/work",
                                sandbox="workspace-write", approval_policy="never", instructions="trusted")
    assert "model" not in delivery.settings
    jsonschema.validate(delivery.settings, schema("v2/ThreadStartParams"))
    jsonschema.validate({**delivery.settings, "threadId": "thread-test"}, schema("v2/ThreadResumeParams"))


def test_client_payloads_and_cancellation_responses_match_schema():
    delivery = AppServerDelivery(AppServerClient(["codex", "app-server"]), model="fixture", cwd="/work",
                                sandbox="workspace-write", approval_policy="never", instructions="trusted")
    delivery.thread_id = "thread-test"
    jsonschema.validate(delivery.settings, schema("v2/ThreadStartParams"))
    jsonschema.validate({**delivery.settings, "threadId": delivery.thread_id}, schema("v2/ThreadResumeParams"))
    params = delivery.turn_params({"event_id": "event-test", "lane": "mail", "notice_id": "notice-test"})
    jsonschema.validate(params, schema("v2/TurnStartParams"))
    assert params["input"] == []
    responses = {
        "CommandExecutionRequestApprovalResponse": {"decision": "cancel"},
        "FileChangeRequestApprovalResponse": {"decision": "cancel"},
        "PermissionsRequestApprovalResponse": {"permissions": {}, "scope": "turn"},
        "ExecCommandApprovalResponse": {"decision": "abort"},
        "ApplyPatchApprovalResponse": {"decision": "abort"},
        "McpServerElicitationRequestResponse": {"action": "cancel"},
    }
    for name, response in responses.items():
        jsonschema.validate(response, schema(name))


def test_maximum_default_binary_attachment_output_fits_frame(tmp_path, monkeypatch):
    """25 MiB binary bytes resolve locally; the app-server frame carries a path."""
    loader = importlib.machinery.SourceFileLoader("amap_reader_frame_test", str(ROOT / "bin/inbox-mcp-vol"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    reader = importlib.util.module_from_spec(spec)
    loader.exec_module(reader)
    messages = tmp_path / "mail/messages"
    messages.mkdir(parents=True)
    sidecar = tmp_path / "mail/notices/max-file.attachments/0"
    sidecar.parent.mkdir(parents=True)
    size = 25 * 1024 * 1024
    with sidecar.open("wb") as stream:
        stream.seek(size - 1)
        stream.write(b"\0")
    with sidecar.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (messages / "notice-max-file.json").write_text(json.dumps({
        "contract_version": "2", "notice_id": "max-file", "body_text": "synthetic",
        "attachments": [{"filename": "sample.bin", "media_type": "application/octet-stream",
                         "size_bytes": size, "sha256": digest, "disposition": "clean",
                         "content_ref": "max-file.attachments/0"}]}))
    monkeypatch.setenv("INBOX_MESSAGE_DIR", str(messages))
    monkeypatch.setenv("INBOX_LANE", "mail")
    result = reader.tool_read_attachment({"message_id": "max-file", "index": 0})
    assert not result.get("isError")
    assert str(sidecar) in result["content"][0]["text"]
    encoded = json.dumps({"method": "item/completed", "params": {"item": {"result": result}}}).encode()
    assert len(encoded) < 4096
    assert len(encoded) < Config.__dataclass_fields__["frame_limit_bytes"].default
