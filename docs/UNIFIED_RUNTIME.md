# Unified runtime engineering contract

Status: experimental source implementation, pending independent review and
adapter integration. The synthetic native runtime probe covers the service,
not deployment acceptance. Existing-history adoption and audited native queue
disposition remain prerequisites for an existing-fleet rollout. Ordinary
`recover` commands currently refuse unified mode rather than marking a retained
native queue entry handled while leaving it runnable.

## Configuration and commands

Keep the existing namespace, protected instructions, trusted MCP configuration
and private state settings. Opt in explicitly:

```toml
conversation_mode = "unified"
unified_argv = ["codex"]
operator_kickoff_enabled = false
```

`unified_argv` is the native executable and any protected backend configuration
options, before `app-server`. It is an argument vector, never shell text. The
existing `launch_argv` remains in configuration for the separate-mode contract;
unified mode launches its own managed Unix backend using `unified_argv`.
`codex_model`, `sandbox` and `approval_policy` resolve the shared pane settings.
An explicit native model or permission flag must match them. An audited model
change on service restart keeps the canonical thread; protected policy changes
continue to require an explicit transition.

The service uses the existing CLI commands:

```sh
amap-codex --config /etc/amap-connector/connector.toml doctor
amap-codex --config /etc/amap-connector/connector.toml run
```

The adapter manages `run` independently of terminal attachment. In another
terminal in that same execution environment:

```sh
amap-codex --config /etc/amap-connector/connector.toml status
amap-codex --config /etc/amap-connector/connector.toml interactive -- codex resume --last
```

`interactive-args` prints the same validated argument vector without creating
or resuming a backend. Both a fresh launch and `resume --last` attach to the
registered canonical ID. An explicit foreign ID is refused. Native
`--no-daemon` is consumed in favor of the registered endpoint. Matching
permission flags are validated and consumed: native remote resume rejects CLI
permission overrides and inherits the managed thread's verified policy.
Model selection is reflected in the actual native client and backend.

The wrapper waits at most 30 seconds for readiness, then reports a hold. It
never starts another backend or chooses an ambient conversation. The gateway
is authoritative for the one-client lease, including simultaneous attach races.
Closing a terminal preserves the service, incoming work and conversation.

These commands describe the new source contract, not commands to apply an
unreviewed change to a running fleet. Mode changes keep the journal and require
the existing explicit migration rules; do not delete state to activate this mode.

## Native ownership and recovery

The controller holds the normal instance lock and every enabled AMAP claim.
Each backend execution gets a fresh private directory and endpoint generation.
The native endpoint may be an owned alias into a private native socket
directory; its parent, alias owner, target parent, target owner/type and private
permissions are checked. Only the freshly launched explicit endpoint is used.
The terminal gateway itself is a real private socket in the instance state.

Human interaction stays native. The gateway restricts conversation selection
and protected configuration; it does not implement turn scheduling. Native
start/steer/interrupt and queue operations remain native. New/fork/revert,
arbitrary developer/tool configuration injection and model/permission changes
that disagree with the service are refused. Built-in default/plan collaboration
modes use the backend's own instructions, not developer text supplied by a
client. Additional native controls need their own verified contract.

AMAP submission commits the attempt, client ID, exact pointer input, canonical
thread and dispatch generation atomically before writing. Queue admission is
recorded as `queued`, distinct from original-input acceptance and completed
execution. Repeated adds with one client ID are not idempotent. Missing,
ambiguous, duplicated or changed evidence never authorizes another add.
Only positive local evidence that no bytes were sent permits bounded retry.

Before resuming a saved canonical thread, inspect its persisted queue without
loading it. Refuse duplicated IDs, inputs already present in original history,
recorded accepted/finished work still pending, changed pointer associations,
blocked intents or unavailable/changed retained artifacts. This check precedes
Codex's automatic draining of persisted queued work on resume.

Reconciliation pages both turns and full items, as well as the pending queue.
It checks canonical identity, unique original `userMessage.clientId`, complete
input bytes, turn association and execution status. Current bounds are 100
pages of 100 entries and 64 MiB of JSON per paginated observation. Exceeding a
bound holds observation; it does not establish absence. Interleaving native
turns can temporarily invalidate a snapshot. Such a read gap holds new dispatch
and attachment, publishes unknown facts, and leaves authorized work running.

The passive controller connection never answers approval/user-input requests.
An attached native terminal handles them; without one the backend holds. Replies
must belong to the current frontend connection and its server request IDs.
Transport failure never triggers RPC replay or a private model conversation.

## Adapter status and installation handoff

The adapter installs the complete connector package and renders the opt-in
configuration; it does not implement Codex scheduling. No isolation-specific
paths, terminal multiplexer or host execution endpoint is assumed. Initial
unified mode requires the controller, native backend and terminal to share an
execution environment and local Unix namespace. Separate mode retains the
existing controlled host-side launcher support; that transport is not silently
reused for a remote unified backend.

The instance state directory must be private, writable and short enough for
Unix socket paths (100 bytes for the generated native endpoint). Keep payload
files and operator policy protected. Keep runtime sockets out of lane roots and
shared workspaces. Do not edit shared authentication or global MCP registration.

`status` exposes schema version 1 facts, sourced from the matching journal and
service snapshot. `unified.json` is the private attachment descriptor:

| Field | Meaning |
| --- | --- |
| `mode` | `unified` |
| `backend_generation` | Unique owned backend execution |
| `canonical_thread_id` | Journal-bound conversation |
| `endpoint` | Exact private terminal gateway URI |
| `attached_client_count` | Zero or one authoritative gateway lease |
| `scheduler_state` | Native state, holding or reconciling |
| `active_input_source` | Operator/mail/peer when established, otherwise null |
| `queue_depth`, `queue_sources` | Observed native queue, otherwise null |
| `effective_settings` | Model, permissions and configuration sources |
| `readiness` | Ready/holding/stopped with named pass/fail/unknown facts |

Readiness requires the matching live backend, canonical binding, current
configuration, validated protected tools, reconciled native state and no
ambiguous outcome publication. Optional unconfigured outcomes are not a send or
publication success. Missing facts, stale heartbeats, changed builds/models,
foreign generations and mismatched journal bindings do not become ready defaults.
Retain the adapter's bounded startup hold and service logs.

Prepare adapter changes as unmerged PRs for both its current development line
and the deployed release line. Preserve unrelated release pins. Supply an
operator runbook only after source review and remaining recovery/adoption gates
pass; the operator performs installation and agent stop/start. No fleet lifecycle
change is part of these connector commands or probes.

## Verification

```sh
python -m pytest tests
python vendor/amap-spec/fixtures/validate.py
PYTHONPATH=src python tests/integration/unified_runtime_probe.py --output /tmp/unified-runtime.json
```

The opt-in native probe uses disposable homes and deterministic localhost
Responses events. It covers busy human/peer visibility, detached mail, one-client
refusal, exact history across restart, stale generations, native approval decline,
an audited model restart, pending work across SIGKILL and refusal of changed
queued artifacts before resume. It uses no user credentials, external inference,
router or fleet traffic. It also executes the authenticated reader, inert submit
and pending-result check through the actual protected MCP tools, with the final
response visible in the same pane. Full history adoption, audited native queue
disposition, actual router acceptance/replies and deployment acceptance remain
distinct gates.

```sh
python tests/mutations/unified_runtime.py
```

This inventory disables 52 critical transport, ownership, queue, policy and
attachment guards on independent scratch copies and requires the negative
controls to detect each mutation. It never mutates repository source files.
