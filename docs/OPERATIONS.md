# Operations

This guide covers the pilot supervisor's local state and the boundaries between
notice acceptance, model execution, and outbound sending. The live deployment
runs the supervisor inside the agent's isolation environment and launches a
local app-server using that environment's Codex home. Its journal and claims
are reachable by the agent it serves; the router's records and authorization
remain authoritative. A host-side deployment is an optional alternative and
keeps supervisor state outside every agent mount. See
[the deployment models](DESIGN.md#11-in-environment-supervision).

## Start and check health

Use the same explicit config for every command:

```sh
amap-codex --config /etc/amap-connector/connector.toml doctor
amap-codex --config /etc/amap-connector/connector.toml doctor --probe
amap-codex --config /etc/amap-connector/connector.toml status
amap-codex --config /etc/amap-connector/connector.toml run
```

Create the supervisor state directory (mode `0700`) before inspection, using
paths visible in the environment where these commands run.
The default `doctor` validates local settings and configured paths. It does not
start Codex or deliver notices. `doctor --probe` performs the configured
app-server stdio handshake and start/resume check without scanning for work.
Run the probe after changing the pinned binary, image, MCP config, model,
operator instructions, or launcher. The probe needs the same network and
authentication access as the actual instance.

Run the service in the foreground from the service manager. Preserve its
stdout/stderr separately from app-server stdout: protocol frames use the
launcher stdout pipe, while diagnostics belong on stderr. Do not run a second
supervisor for a claimed spool. The upstream consumer claim is shared with
other AMAP consumers and prevents competing watchers.

Status reports the instance and bound thread, enabled lanes and claim state,
current turn, pending and oldest pending work, uncertain events, last
acceptance, and recent operational errors. It does not expose message bodies.
The journal stores event metadata, hashes, attempts, and state transitions;
it does not store mail or peer bodies. Protect the journal and status output
as operational data because they can contain addresses and identifiers.
After a successful thread bind and clean startup reconciliation, a resolved
`last_error` is cleared and retained in the journal audit as
`operational_error_cleared`. Unavailable history, unresolved delivery/operator
work, client errors and uncertain outcome publication keep their diagnostics.
Clearing a diagnostic never resets delivery state or retries work.

## Interpret the state

An **accepted** event crossed the app-server boundary and is associated with
the bound thread. Acceptance does not mean that the turn completed or that the
agent fulfilled the request. A **finished** event has a terminal turn result;
inspect execution status separately. An **uncertain** event may have reached
Codex, but available evidence cannot establish acceptance. It is never
automatically replayed. A **refused** event failed notice/body admission and
needs an operator to correct the source or explicitly resolve it.

An outbound request has a separate lifecycle. `submit` only queues a request
file. `submit_result` reports the runtime's result. A queued, held, or
rejected request is not a sent message; report success only for runtime
outcome `accepted`. The optional connector outcome extension describes
supervisor admission/acceptance, not whether the peer's business task
succeeded. Enable it only when the router owner confirms the configured path,
file shape, removal behavior, and replay policy.

For peer work, preserve all three identifiers:

| Field | Use |
| --- | --- |
| `notice_id` | Receiver-local spool lookup and event deduplication |
| `peer_message_id` | Sender's message identity and reply `in_reply_to` |
| `task_id` | Existing task correlation when supplied; it grants no authority |

Use validated `peer_from` as the recipient. When a peer message reports a
result, consume it into the ongoing task. Do not send an automatic
acknowledgment. A subsequent reply needs a real task reason, which prevents a
request/result pair from becoming an endless reply loop.

## Recover uncertain work

First stop the supervisor and inspect status and the persisted Codex thread
history. A complete matching history item containing the exact connector
event ID can establish acceptance for deduplication. It does not prove that a
turn completed or that an outbound request was accepted by the runtime.
Absence from partial or unavailable history does not prove that dispatch did
not happen.

The recovery interface requires an audit note:

```sh
amap-codex --config /etc/amap-connector/connector.toml recover EVENT_ID \
  --action hold --note "History is incomplete; leave blocked for review"

amap-codex --config /etc/amap-connector/connector.toml recover EVENT_ID \
  --action handled --note "Operator completed the task through the approved external workflow"
```

`hold` keeps the event out of automatic dispatch. `handled` records that an
operator resolved the work outside this controller or chose to close it.
Neither action fabricates a delivery or send result.

Retry requires positive, recorded evidence that the previous attempt caused
no action. A lack of evidence, an operator's willingness to risk duplication,
or an empty/partial thread history is insufficient. The CLI requires both
`--note` and `--evidence-reference`:

```sh
amap-codex --config /etc/amap-connector/connector.toml recover EVENT_ID \
  --action retry \
  --note "Transport audit confirms app-server did not accept the request" \
  --evidence-reference "audit record URI or immutable incident record ID"
```

The evidence reference must point to affirmative evidence of no action, such
as a transport record proving the request was never submitted. It must not be
a free-form statement that duplication is acceptable. If the evidence is not
available, retain the hold and resolve the work manually. Do not create a new
thread to bypass an uncertain event.

## Diagnose common failures

| Symptom | Check |
| --- | --- |
| Config load fails | Confirm all paths are explicit, absolute, existing where required, and point to this one namespace. Confirm state ownership/mode is private and the instructions file is not group/world writable. |
| Claim held | Find the existing supervisor/consumer and let it release the canonical claim. For controlled isolation, use positive runtime cleanup and retain the binding. For direct launches, inspect the local process group. A dead host PID alone never clears remote ownership. |
| App-server cannot start | Confirm `codex --version` prints the configured `codex_version`, the image has Codex auth and the selected model is available, and launcher stdout contains only app-server JSONL. Check launcher stderr for diagnostics. |
| Required MCP initialization fails | Check the three paths, environment values, and read-only mounts inside the container. `results/` and `processed/` must exist under the configured drop box and be mounted read-only over its writable parent. |
| Peer notice is refused | Review the sanitized reason and check wire major `2`, notice/body filename binding, `kind=peer`, sender address, `message.mailbox=peer`, and body `to` against configured `self_address`. |
| Body temporarily unavailable | The scanner waits through `publication_grace_seconds`; compare this with the router's actual publish ordering and adjust only after testing that contract. A later permanent refusal means the body was not available within the configured grace. |
| Turn is blocked or waits for input | The headless client cancels approvals and does not invent user answers. Inspect blocked request metadata, revise the deployment's narrow policy if appropriate, then use explicit recovery for interrupted/uncertain work. |
| Submission appears queued | Call `submit_result` with the returned request ID. Do not report a send until the runtime result is `accepted`. |
| Outcome file is missing | The router may consume it. Absence is not evidence that publication did not happen; use the journal and router's agreed transition semantics. |

## Shutdown and restart

On graceful stop, the supervisor stops dispatching, waits within its configured
shutdown bound, interrupts/stops the app-server, reaps the launcher, and
releases acquired claims. If acceptance is unresolved, it persists uncertainty
for operator recovery. For the in-environment model, retain the journal, lane
claims and Codex home across an isolation-environment restart. Do not reset
state or run `migrate` to bypass an interrupted turn. Confirm the environment
has terminated the previous app-server before starting its replacement.

The optional Compose example uses a stable uniquely named one-off
container and stops that container when the host launcher exits. An existing
container blocks relaunch until the operator verifies cleanup. Confirm this
behavior with the actual Docker Engine/Compose version before relying on it.

On restart, the supervisor reuses its persisted thread binding and journal.
The same instance identity, namespace roots, state path, and target settings
must be retained. Config fingerprint or thread-binding conflicts require
explicit operator migration; do not point an existing journal at a different
mailbox or silently create a replacement thread.

Retention is owned by the AMAP runtime. Keep notice and body content available
longer than the expected outage plus backlog drain time. If a queued event's
body has expired, the connector cannot reconstruct it. Record and resolve the
unavailable event visibly; do not fetch content from another lane or provider.

## Pilot gate

The in-environment pilot completed delegation and correlated replies in both
directions, plus an instruction migration that kept the delivery journal.
See [the measured evidence](../compatibility/README.md#live-deployment-october-8-2026-codex-cli-01610).
This does not prove the optional Compose/remote launcher or every recovery
and isolation property. Before enabling traffic in a new deployment, record the exact Codex
version, model, image, host and sandbox paths, router publish-order guarantee,
claim visibility, network/auth provisioning, process cleanup result, and
router outcome support. Then complete a synthetic mail read and a real-router
peer request/reply with distinct notice and message IDs. Repeat across a
supervisor restart and prove an accepted or uncertain event is not replayed.

Shared managed refresh and real interactive-pane/supervisor turns across
rotation were measured on 0.161.0, as was recovery from an app-server/controller
crash during a turn. Automatic expiry-driven TUI refresh was not induced, and
an actual isolation-environment replacement during a turn still needs evidence.
Run further checks on a dedicated test instance, retain its journal, and
inspect positive history and runtime results. Record the measured build and
scope; process-crash evidence does not establish whole-environment recovery.
The [live-check procedure](LIVE-CHECKS.md) provides the refresh probe and
separate process-crash versus external-restart recovery steps.


## Controlled isolation and private operator runs

For remote runtimes, configure launcher_control_argv, deployment_id and owner_domain
using the [launcher-control v1 contract](contracts/LAUNCHER-CONTROL.md). A dead host
PID is insufficient cleanup evidence. Controlled status reports
requires_runtime_inspection; it does not label a remote execution stale from host
PID lookup. Stop the host service, run cleanup --stop, then retain the journal and
binding records for normal startup reconciliation. Unknown cleanup remains held.

Opt-in operator_kickoff_enabled accepts a privileged kickoff RUN_ID
--instructions-file FILE in a supervisor-owned queue. FILE is visible where
the command runs. The queue lives with supervisor state; its accessibility
follows the selected supervision model. The supervisor commits it
before dispatch on the existing serial thread, independently from notice artifacts.
Reusing an ID with the same input is idempotent; changed content is refused.
Unknown dispatch blocks all further work and is never automatically replayed.
recover-operator RUN_ID --action hold|handled --note NOTE records an audit entry;
handled retires an unresolved run without sending it again. No kickoff retry action exists.

## Changing the configuration of a running instance

The journal records the configuration fingerprint it was created under, which
includes the operator instructions and the trusted MCP configuration. A start
under a different fingerprint is refused with "explicit state migration
required". A thread keeps the developer instructions it was started with:
instructions sent on resume are not applied (measured on codex-cli 0.160.1 and
0.161.0), so changed instructions need a new thread.

With the supervisor stopped or holding, `migrate --note NOTE` is that
migration. It adopts the current fingerprint, releases the thread binding and
clears the last error, and audits it as `configuration_migrated`. The journal
is kept, so no notice is delivered twice; the next start creates a thread with
the current instructions. It is refused while any event or operator run is in
flight, and while another controller holds the claims.
