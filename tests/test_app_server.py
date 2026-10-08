import asyncio
import json
import sys
from pathlib import Path

import pytest

from amap_codex.app_server import AppServerClient, AppServerDelivery, find_event_in_history

FAKE = str(Path(__file__).with_name("fake_app_server.py"))
PAYLOAD = {"event_id": "event-exact", "lane": "mail", "notice_id": "0123456789abcdef" * 2}


def adapter(mode="normal", path=None, timeout=0.5, frame_limit=64 * 1024 * 1024):
    argv = [sys.executable, FAKE, mode] + ([str(path)] if path else [])
    client = AppServerClient(argv, timeout=timeout, frame_limit=frame_limit)
    return AppServerDelivery(client, model="fixture", cwd="/work", sandbox="workspace-write",
                             approval_policy="on-request", instructions="trusted workflow")


def test_initialize_accept_complete_and_reap():
    async def check():
        delivery = adapter()
        try:
            assert await delivery.start_or_resume() == "thread-fixture"
            result = await delivery.deliver(PAYLOAD, delivery.client.reserve_id())
            assert result.state == "accepted"
            frame = await asyncio.wait_for(delivery.client.notifications.get(), 1)
            assert frame["params"]["turn"]["status"] == "completed"
        finally:
            await delivery.stop()
        assert delivery.client.process.returncode is not None
    asyncio.run(check())


@pytest.mark.parametrize("mode", ["death", "timeout", "rpc_error", "malformed", "oversized"])
def test_ambiguous_failures_never_report_unsent(mode):
    async def check():
        delivery = adapter(mode, frame_limit=4096)
        try:
            await delivery.start_or_resume()
            result = await delivery.deliver(PAYLOAD, delivery.client.reserve_id())
            assert result.state == "uncertain"
        finally:
            await delivery.stop()
    asyncio.run(check())


def test_dead_before_write_is_proven_unsent():
    async def check():
        delivery = adapter()
        await delivery.start_or_resume()
        await delivery.stop()
        assert (await delivery.deliver(PAYLOAD, delivery.client.reserve_id())).state == "not_submitted"
    asyncio.run(check())


@pytest.mark.parametrize("mode", ["approval", "user_input"])
def test_server_requests_do_not_block_reader_or_autoapprove(mode):
    async def check():
        delivery = adapter(mode)
        try:
            await delivery.start_or_resume()
            assert (await delivery.deliver(PAYLOAD, delivery.client.reserve_id())).state == "accepted"
            frame = await asyncio.wait_for(delivery.client.notifications.get(), 1)
            assert frame["params"]["turn"]["status"] == "interrupted"
            assert delivery.client.blocked
            assert delivery.client.last_error.startswith("headless request blocked")
        finally:
            await delivery.stop()
    asyncio.run(check())


def test_resume_persisted_original_input(tmp_path):
    async def check():
        path = tmp_path / "history.json"
        first = adapter(path=path)
        await first.start_or_resume()
        await first.deliver(PAYLOAD, first.client.reserve_id())
        await first.stop()
        second = adapter(path=path)
        try:
            await second.start_or_resume("thread-fixture")
            history = await second.observe()
            assert find_event_in_history(history, "event-exact")[0] == "turn-1"
        finally:
            await second.stop()
    asyncio.run(check())


def test_history_does_not_accept_assistant_echo_or_substring():
    history = {"thread": {"turns": [{"id": "turn-1", "status": "completed", "items": [
        {"type": "agentMessage", "text": json.dumps(PAYLOAD)},
        {"type": "functionCallOutput", "name": "amap_notice", "namespace": "evil",
         "output": json.dumps(PAYLOAD)},
        {"type": "functionCallOutput", "name": "amap_notice", "namespace": "amap_connector",
         "output": json.dumps({"event_id": "event-exact-extra"})},
    ]}]}}
    assert find_event_in_history(history, "event-exact") is None


def test_wake_up_contains_only_validated_pointer():
    delivery = adapter()
    delivery.mode = "wake_up"
    params = delivery.turn_params(PAYLOAD)
    assert "toolOutput" not in params
    assert params["input"][0]["text"].endswith(json.dumps(PAYLOAD, sort_keys=True, separators=(",", ":")))


def test_blocked_protocol_write_is_bounded_and_uncertain(monkeypatch):
    async def check():
        delivery = adapter(timeout=0.05)
        try:
            await delivery.start_or_resume()
            async def stalled_send(frame):
                await asyncio.Event().wait()
            monkeypatch.setattr(delivery.client, "_send", stalled_send)
            result = await asyncio.wait_for(delivery.deliver(PAYLOAD, delivery.client.reserve_id()), 0.5)
            assert result.state == "uncertain"
            assert "write timeout" in result.detail
        finally:
            await delivery.stop()
    asyncio.run(check())


TRUSTED = {"mcp_servers": {
    "inbox": {"enabled_tools": ["list_messages", "read_message", "read_attachment"]},
    "delegation": {"enabled_tools": ["list_messages", "read_message", "read_attachment"]},
    "inbox_submit": {"enabled_tools": ["submit", "submit_result", "peers"]}}}


def trusted_adapter(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    client = AppServerClient([sys.executable, FAKE, "normal"], timeout=2)
    return AppServerDelivery(client, model=None, cwd="/work", sandbox="workspace-write",
                             approval_policy="never", instructions="trusted", trusted_config=TRUSTED)


def start(delivery):
    async def check():
        try:
            return await delivery.start_or_resume()
        finally:
            await delivery.stop()
    return asyncio.run(check())


def test_the_agents_own_mcp_servers_are_switched_off_for_the_thread(monkeypatch):
    delivery = trusted_adapter(monkeypatch, FAKE_AMBIENT_MCP=json.dumps(
        {"agent_own": {"command": "/bin/true"}, "agent_off": {"command": "/x", "enabled": False}}))
    assert start(delivery) == "thread-fixture"
    assert delivery.disabled_servers == ["agent_off", "agent_own"]
    assert delivery.trusted_config == TRUSTED, "the trusted configuration itself is not changed"


def test_no_agent_servers_starts_with_exactly_the_trusted_three(monkeypatch):
    assert start(trusted_adapter(monkeypatch)) == "thread-fixture"


def test_an_agent_server_with_a_trusted_name_is_refused(monkeypatch):
    delivery = trusted_adapter(monkeypatch, FAKE_AMBIENT_MCP=json.dumps({"inbox": {"command": "/bin/true"}}))
    with pytest.raises(RuntimeError, match="rename them"):
        start(delivery)


def test_a_switched_off_server_that_keeps_its_tools_is_refused(monkeypatch):
    delivery = trusted_adapter(monkeypatch, FAKE_AMBIENT_MCP=json.dumps({"agent_own": {"command": "/bin/true"}}),
                               FAKE_IGNORE_DISABLE="1")
    with pytest.raises(RuntimeError, match="still exposes tools"):
        start(delivery)


def test_a_registration_config_read_did_not_report_is_refused(monkeypatch):
    delivery = trusted_adapter(monkeypatch, FAKE_UNREPORTED_MCP="project_own")
    with pytest.raises(RuntimeError, match="unexpected \\['project_own'\\]"):
        start(delivery)


def test_codex_apps_stays_out_of_the_thread_under_a_chatgpt_login(monkeypatch):
    delivery = trusted_adapter(monkeypatch, FAKE_CHATGPT_APPS="1")
    assert start(delivery) == "thread-fixture"
    assert delivery.disabled_servers == []
    assert "features" not in delivery.trusted_config, "the trusted configuration itself is not changed"
