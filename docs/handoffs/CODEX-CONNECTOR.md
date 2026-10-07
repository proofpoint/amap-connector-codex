# Generic Codex connector handoff

Implementation status: engineering changes are prepared and locally verified.
See [verification and PRs](../ENGINEERING-VERIFICATION.md) and the
[operator runbook](../OPERATOR-RUNBOOK.md). The requirements below remain the
review/acceptance contract; host-only gates are pending, and nothing is auto merged.


Implement the isolation-independent integration work needed by
[the dedicated sandbox rollout](../ROLLOUT.md). Keep the existing notice
validation, filesystem/MCP contract and serial persistent-thread ownership.

## Requested changes

1. Publish a bounded, versioned launcher-control schema before downstream
   implementation. Keep `launch_argv` for stdio execution and add optional
   `launcher_control_argv` with fixed `inspect` and `stop` verbs, JSON stdin,
   one JSON stdout result, explicit timeout and stderr diagnostics. Bind
   deployment and execution identities; control requests contain no sender data.
2. Persist isolated execution identity before admitting work. On host restart,
   inspect surviving execution before reclaiming ownership or launching another
   controller. Do not clear process/ownership records because the local launcher
   was reaped; require positive termination of the actual app-server and children.
   Interrupted stop/inspection remains unknown and blocks new execution.
3. Adapt claims to canonical host guards shared by all supported consumers of
   a spool. Include owner domain and execution binding. Missing/foreign PID
   domains and a surviving remote execution are not stale. Define the migration
   contract for legacy Claude claims with the deployment handoff; use the
   deployment/runtime inspector for positive liveness evidence.
4. Support trusted MCP startup configuration without hardcoded isolation paths.
   Retain operator instructions on start/resume and exact tool allowlists. Let
   the deployment supply immutable configuration or a pinned-build-supported
   config override. Preserve the historical default configuration/fingerprint
   where new optional fields are absent; target changes still require migration.
5. Add an opt-in live test kickoff for the Codex-initiated round trip. A privileged
   local integration harness must use the supervisor's existing client, thread,
   lease and idle queue. Record a stable operator-run ID and committed dispatch
   attempt before `turn/start`; persist acceptance and terminal result. Track
   operator records separately from router-authored mail/peer artifacts. Unknown
   acceptance blocks replay and further dispatch just as for notice delivery.
   Host operator instructions may initiate the synthetic send; inbound body text
   must never acquire that authority. Publish the actual harness invocation when
   implemented; the CLI now provides `kickoff RUN_ID --instructions-file HOST_FILE`.
6. Add opt-in integration fixtures for both request/reply directions, synthetic
   attachments, runtime send results, restart and interrupted dispatch. Correlate
   receiver notice ID, sender message ID, turn ID and operator run ID separately.

## Scope and portability

Core modules must not import Sandy, run Docker commands, read `.sandy` or
`/etc/sandy-session.json`, derive slugs, install services or create sandbox
mounts. A single concrete app-server adapter remains sufficient. A direct local
launcher is supported alongside externally controlled execution; no runtime
plugin registry or cloud service is required.

Keep the launcher protocol and conformance fixtures under connector ownership.
The Sandy deployment implements the adapter; other deployments can implement
the same contract. Use explicit configuration for paths, permission provisioning
and identity. Exercise host execution on macOS and Linux and isolated app-server
execution on the existing pinned build.

## Acceptance criteria

- Existing deterministic and imported regression suites pass unchanged except
  for targeted, documented expectations.
- A non-Sandy launcher passes start/resume, stdio, cleanup, orphan, cancellation
  and restart conformance checks.
- Killing a host launcher while remote execution survives cannot permit another
  controller, and foreign/legacy claims cannot be reclaimed by host PID guessing.
- Operator kickoff and notice delivery are mutually serial, durably associated
  with one thread, and never automatically replay uncertain work.
- Resumed inference demonstrates the effective workflow and required MCP tools.
- Peer replies use `peer_message_id`; correlated results produce no automatic
  acknowledgment loop. Queued/held/rejected submissions are not reported as sent.

Deliver the schema, optional example configuration, deterministic fixtures,
documented live harness command, compatibility evidence and migration notes.
Lifecycle and crash-window review should precede rollout configuration work.
