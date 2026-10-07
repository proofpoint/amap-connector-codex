# Upstream source imports

## `proofpoint/amap-connector-claude`

- Source: <https://github.com/proofpoint/amap-connector-claude>
- Pinned commit: `ca9b40647c5c002e6e6fe30b4fea54e64a76af57`
- License: Apache-2.0, preserved in the repository-root `LICENSE`.
- Imported files: `bin/inbox-mcp-vol`, `bin/inbox-submit`, and `bin/_inboxlib.py` under `bin/`; consumer-claim implementation adapted as `src/amap_codex/claims.py`; focused upstream tests under `tests/upstream/`.
- Local differences: the inbound reader requires a present wire `contract_version` with major `2` before using a published message, refusing missing, malformed, lower, or higher majors as required by the pinned AMAP contract. Submit MCP metadata now describes the Codex connector wake-up flow and keeps local `notice_id` distinct from sender `peer_message_id`; Claude `SendMessage` guidance was removed. The submit MCP server identifies as `amap-codex-submit`.
- Corrected inherited attachment prose: approval remains the runtime relay's decision;
  the connector does not require blanket human approval or claim content scanning.

## `proofpoint/amap-spec`

- Source: <https://github.com/proofpoint/amap-spec>
- Pinned commit: `2ffdfeb06631329fad3678788d03e854c92ba98b`
- License: Apache-2.0, preserved at `vendor/amap-spec/LICENSE`.
- Imported files: core mail, peer, directory, submit, result, and roster schemas under `vendor/amap-spec/schemas/`; the upstream fixture validator and representative valid/invalid fixtures under `vendor/amap-spec/fixtures/`.
- Local differences: this is a focused schema/fixture subset for the imported reader/submit tests, not a complete copy of the specification repository.
