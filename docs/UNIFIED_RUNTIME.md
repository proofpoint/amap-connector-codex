# Unified runtime engineering contract

Status: experimental source implementation. The synthetic native runtime probe
covers the service, not deployment acceptance. [Explicit history adoption and
audited queue disposition](UNIFIED_MAINTENANCE.md) have separate stopped-service
native evidence. Adapter bootstrap and actual-host acceptance remain distinct
gates. Unified `recover` supports explicit hold/handled disposition and optional
pending cancellation; it refuses retry.

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
Native workspace evidence must be a nonempty absolute path matching the configured
workspace. Missing, empty or relative native paths never fall back to the local
service process directory.
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

The wrapper waits at most 30 seconds for readiness, then reports the specific
hold (for example stale heartbeat or mismatched startup validation). Adapters
should use this one wait instead of adding another readiness polling timeout. It
never starts another backend or chooses an ambient conversation. The gateway
is authoritative for the one-client lease, including simultaneous attach races.
Closing a terminal preserves the service, incoming work and conversation.

These commands describe the new source contract, not commands to apply an
unreviewed change to a running fleet. Mode changes keep the journal and require
the existing explicit migration rules; do not delete state to activate this mode.

### Fresh instances and deployment defaults

A deployment may select unified mode once for its fleet, then render the explicit
configuration above for every newly created Codex instance. No per-workspace
thread UUID or saved-conversation adoption is required for a genuinely fresh
instance. The connector's separate-mode configuration remains supported; the
deployment's fleet policy chooses the default.

`UnifiedService.start` creates a persistent native conversation when the instance
has no canonical binding. It names the empty conversation so the measured native
build persists it, validates its protected tools and effective settings, and
commits the native thread ID and policy binding before accepting inputs. Creating
the conversation submits no human or AMAP model input. Fresh terminal startup
and `resume --last` attach to this registered conversation; neither selects an
ambient saved history. Subsequent launches retain the same canonical binding.

An adapter must distinguish fresh initialization from history adoption. Its
durable initialization record must establish the intended namespace, workspace,
protected configuration and settings before enabling startup. An existing
separate-mode journal, previous binding, unfinished transition or unknown retained
state is not proof of freshness. Refuse it with explicit operator instructions;
never clear it, adopt another history or fabricate adoption confirmation.

Incoming notices can arrive before the adapter entry starts. Preserve that work
without executing it until startup validation succeeds; pending inbound work
alone does not establish that a previous consumer ran. Initialization custody
must survive any supported reset that retains the delivery namespace. If a
reset removes the conversation state while retaining that custody record, hold
for operator recovery rather than initializing another conversation over old
input. Include this case and queued-before-startup delivery in adapter tests.

Local absence of files cannot establish that an external router retired an old
namespace. Reusing an address after recreation requires the deployment's verified
custody procedure. Complete erasure of all initialization and delivery evidence
is not an automatic recovery path.

If the adapter validates complete native startup argv before releasing incoming
work, that validation must also work before a fresh canonical UUID exists. Bind
the selection to the protected configuration and execution generation, permit
only the owned canonical target, and validate the resulting journal binding and
service readiness before attachment. Missing or conflicting evidence holds
startup. Do not introduce a separate temporary conversation to break the
bootstrap dependency.

The operator may choose to retire and recreate old instances instead of migrating
their history. That is an explicit deployment lifecycle action. Installing source
or selecting a new default does not authorize deleting old state, resetting
delivery journals, cancelling pending work or replaying messages.

## Native ownership and recovery

The controller holds the normal instance lock and every enabled AMAP claim.
Each backend execution gets a fresh private directory and endpoint generation.
The requested native endpoint is `state_dir/u-<32 hex generation>/b`; the
terminal gateway is the same generation's `/t`. The native endpoint may be an owned alias into a private native socket
directory; its parent, alias owner, target parent, target owner/type and private
permissions and the resolved target's 100-byte socket-path bound are checked.
The native build chooses the target directory; adapters must not derive its
name or discover another daemon through it. Only the freshly launched explicit endpoint is used.
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
Either a known queue ID or a known client ID retains AMAP custody. A changed
association is refused before the unrelated-human-input exception, including
when the retained event was completed or its body is no longer available.

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

Both separate and unified `status` declare `schema_version = 1` and `mode`;
[the JSON Schema](status.schema.json) describes the additive public format,
including a not-started or held instance. Schema validation alone does not
establish freshness or matching generation/binding.
Public status, activity and attachment share the same ready-evidence validator.
Stale or incomplete public snapshots report unknown readiness; a copied ready
label alone is never accepted.

Unified `status` exposes facts sourced from the matching journal and
service snapshot. `unified.json` is the private attachment descriptor:

| Field | Meaning |
| --- | --- |
| `mode` | `unified` |
| `backend_generation` | Unique owned backend execution |
| `canonical_thread_id` | Journal-bound conversation |
| `endpoint` | Exact private terminal gateway URI |
| `attached_client_count` | Zero or one authoritative gateway lease |
| `scheduler_state` | Native state, holding or reconciling |
| `heartbeat_max_age_seconds` | Published freshness bound: max(10, three polling intervals) |
| `protected_tools_validation` | Startup validation scope, timestamp and backend generation |
| `active_input_source` | Operator/mail/peer when established, otherwise null |
| `queue_depth`, `queue_sources` | Observed native queue, otherwise null |
| `effective_settings` | Model, permissions and configuration sources |
| `readiness` | Ready/holding/stopped with named pass/fail/unknown facts |

The closed scheduler vocabulary is `idle`, `active`, `holding`, `reconciling`,
`unknown`. An unrecognized native state publishes `unknown` and blocks new
dispatch and readiness. `protected_tools` means the protected registry was
successfully validated at startup for this backend generation; it is not a
continuous health check of every tool server. Configuration-changing controls
are refused for attached clients. Tool execution errors remain visible natively.

Readiness requires the matching live backend, canonical binding, current
configuration, validated protected tools, reconciled native state and no
ambiguous outcome publication. Optional unconfigured outcomes are not a send or
publication success. Missing facts, stale heartbeats, changed builds/models,
foreign generations and mismatched journal bindings do not become ready defaults.
Retain the adapter's bounded startup hold and service logs.

The adapter must resolve the pane and service model from the same selection.
If an isolation launcher supplies an explicit model override, render that same
selection into `codex_model` where possible. Otherwise require the override to
be unset or equal to the configured native model and report the mismatch in
verification. Attachment refuses a different model; it never silently changes
the backend to satisfy a terminal flag. The same rule applies to pane permissions.

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

The focused fresh-start probe also proves canonical binding and an idle restart
before any model input, followed by the shared human/peer, detached mail and
retained-history restart scenario:

```sh
PYTHONPATH=src python tests/integration/unified_fresh_probe.py --output /tmp/unified-fresh.json
```

Its [build-specific receipt](../compatibility/unified-fresh-0.164.0.json) is
portable connector evidence. It does not validate an adapter's initialization
record, deployment default or operator lifecycle procedure.

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

This inventory disables 69 critical transport, ownership, queue, policy and
attachment guards on independent scratch copies and requires the negative
controls to detect each mutation. It never mutates repository source files.
