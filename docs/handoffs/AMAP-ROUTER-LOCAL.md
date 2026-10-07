# Generic router outcome handoff

Implementation status: engineering changes are prepared and locally verified.
See [verification and PRs](../ENGINEERING-VERIFICATION.md) and the
[operator runbook](../OPERATOR-RUNBOOK.md). The requirements below remain the
review/acceptance contract; host-only gates are pending, and nothing is auto merged.


Allow deployments to opt into Codex connector outcomes while retaining the
existing Claude behavior and AMAP transport/policy semantics. This supports
[the dedicated sandbox rollout](../ROLLOUT.md) and other isolation environments.

## Requested change

Add optional top-level configuration `connector_outcome_ids`, a nonempty list
of distinct opaque safe path segments. Default to `["claude-code"]` for existing
configurations. The rollout deployment will explicitly set
`["claude-code", "codex"]`. Reject traversal, separators, empty IDs and malformed
types; IDs identify extension directories and imply no authority or agent kind.

Compute only `outbox_root/ext/<id>/outcomes` for configured IDs. Do not scan
arbitrary extensions or replace the Claude extension with a Codex name. Preserve
the combined per-instance/poll scan budget and pinned-directory secure reads.
Validate the instance, runtime-issued peer notice and existing outcome document
shape before applying a claim. Outcomes remain informational connector claims.

Preserve transition deduplication by recipient/tree/notice/outcome across every
configured extension. Reading an identical transition from another extension
must not issue another DSN or duplicate business effect. Connector ID may be
included as audit provenance; it must not split the semantic deduplication key.
Acceptance (`delivered`) is independent from execution completion or reply send.

Publish the crash/republication agreement with the connector. The existing
record-before-DSN behavior must retain its conservative semantics: duplicate
publication does not repeat the transition, and a crash must not be described
as guaranteeing eventual DSN delivery. Only after conformance tests establish
safe repeated publication may a deployment enable the connector's
`outcome_idempotent_replay` setting.

## Acceptance criteria

- An existing configuration scans only the historical Claude directory.
- An opted-in configuration consumes valid Claude and Codex outcomes and ignores
  unconfigured directories without treating their absence as success.
- Unsafe connector IDs, symlinks/hard links, malformed documents and unissued
  notice IDs fail existing validation boundaries.
- Cross-directory duplicates, ordinary replay, interrupted recording and budget
  exhaustion preserve deduplication and bounded work. Preserve tested behavior
  for held, refused, inject_failed and delivered transitions.
- All existing router configuration, policy, reply-ledger, first-sight and Claude
  outcome regressions pass with targeted new expectations for configurability.
- A real peer round trip records Codex acceptance and the separate outbound
  reply result with no invented execution-completion attestation.

Update config schema/docs, service consumption, audit/status provenance and
tests. Coordinate the config vocabulary with the Sandy deployment renderer
before it emits the new field. Ship no Sandy imports, container discovery,
model control, credential changes or AMAP message schema changes.

The inspected baseline hardcodes the sole extension in
[outcomes.py](https://github.com/proofpoint/amap-router-local/blob/e43dbba7ac1a44f06b4fbc5dc8c2ffb2777363c0/router/outcomes.py).
Recheck the installed revision before implementation.
