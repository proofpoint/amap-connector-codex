# Unified conversation

Status: engineering in progress. Native-TUI visibility and basic native queue
behavior and the named queue recovery/approval scenarios are measured on Codex
0.164.0. An experimental owned backend, guarded attachment and durable AMAP queue
integration are now implemented; see [the runtime contract](UNIFIED_RUNTIME.md).
History adoption, audited queue disposition, independent review and adapter
integration remain rollout gates. No fleet activation or automatic migration
is provided by this change.

## Decision

The operator and the mail/peer lanes use one canonical conversation per
connector instance. The connector becomes the delivery controller for that
conversation, rather than maintaining a second model conversation. A terminal
is an attachable client of the durable backend; closing it does not transfer
claims or cancel incoming work.

Keep the existing headless/separate mode supported. Unified mode will be an
explicit configuration choice, with its own startup and migration checks. It
must remain independent of any deployment adapter or terminal multiplexer.

Use Codex's native queued-input mechanism as the primary delivery path. The
connector admits durable AMAP notices, queues a protected lookup pointer into
the registered conversation, and reconciles that pointer with execution and
outbound results. Codex owns native turn scheduling and terminal interaction.
Add custom arbitration only where a measured gap requires it; do not build a
second scheduler or intercept every terminal RPC merely to reproduce native
queuing. The reliability gates below must pass before activating this mode.

### Native queue evidence and remaining gates

The native `codex queue --remote <endpoint> --thread <exact-id> --message
<pointer>` command targets the same explicitly owned backend as the terminal.
It is not terminal keystroke injection. Isolated tests with a credential-free
home and synthetic localhost provider have measured:

- Incoming queued work waits for an active human turn to complete.
- Queuing while idle starts a turn whose response appears in the same TUI.
- With the managed backend alive, work executes after terminal detachment and
  reattachment displays its response. The complete ordered earlier history is
  retained without starting another turn.
- The synthetic model, read-only sandbox and never-approve policy are retained.
  A separate read-only/on-request scenario measures approval behavior below;
  broader permission profiles require their own tests.
- Queue text becomes a native `userMessage`, not a tool-output item. Only a
  connector-authored, content-free pointer may use this path; sender text is
  fetched through the authenticated reader under the standing operator policy.

[The native queue probe](../tests/integration/native_queue_probe.py) also measures
distinct pending inputs executing once across a killed backend, lost-ACK
reconciliation without resubmission, pending deletion, active cancellation,
native queue edits/reordering, and native approval holds/reattachment/decline.
Its [build-specific receipt](../compatibility/native-queue-0.164.0.json) records
the exact scope. These are isolated scenarios, not an exactly-once guarantee.

Repeating `thread/queue/add` with the same `clientUserMessageId` creates distinct
pending submissions and both can execute. That field is correlation, not an
idempotency key. Native queue edits preserve it too. Original history exposes
it as `userMessage.clientId`; check exact input content, canonical thread,
observed turn, uniqueness and status as well as the ID. An assistant echo,
matching ID with edited input, or duplicate input is insufficient evidence.

Resume can begin executing pending work before an initial queue read completes.
A decreased pending count alone establishes neither loss nor execution. Native
history and pending queue observations can expose an intermediate state; wait
or hold when the original input is not yet inspectable. Never resubmit on that
basis. The isolated restart test does not replay the interrupted human input.

A native queue receipt proves queue admission, not execution acceptance,
completed work or a delivered reply. The experimental runtime now integrates
the journal, paginated observations, protected settings and attachment checks.
Its authenticated reader/submit probe ends at an inert pending request; actual
router acceptance and replies remain a pilot gate. Audited queue disposition,
history adoption and additional crash/control cases remain engineering gates.

The general collaboration pattern is durable work plus a small native wake.
Reuse AMAP's existing runtime-owned notice files, authenticated readers,
consumer claims and delivery journal. Another pair of mailbox directories,
another message schema or a Claude-specific hook is not required. Explicit
thread registration is portable; Linux `/proc` discovery is not the canonical
binding authority. A brokerless file mailbox does not mean Codex has no backend.

## Foundations

- PR #16 provides safe RPC diagnostics and recovery of a positively missing,
  never-used thread. Unified recovery must count human dispatches too: an empty
  AMAP ledger alone is not proof that an interactive conversation was unused.
- PR #17 provides session identity/workspace checks, protected MCP validation,
  and the measured need to refresh developer policy in established history.
  Its private preflight process, CLI-only target selection and prohibition on
  the supervisor-bound thread belong to separate mode. Unified attachment uses
  the canonical thread on the existing managed backend instead. The two PRs do
  not themselves implement unified delivery, and neither needs deployment as a
  prerequisite to developing this mode.

## Ownership and transport

One controller holds the instance lock and every enabled consumer claim. It
owns one backend and one canonical thread binding. It records the backend
generation, configured build and fingerprint, endpoint, instance identity and
thread ID. A stale descriptor never authorizes attaching to an ambient daemon
or guessing a target with `--last`.

The initial connection contract uses a private local Unix socket in the
instance's execution environment. Codex's Unix listener uses WebSocket framing
and an HTTP Upgrade handshake, not newline-delimited JSON. The feasibility
probe includes a deliberately limited, test-only client. The runtime uses a
separate bounded transport with protocol controls and scratch-copy mutations;
see [its contract](UNIFIED_RUNTIME.md). The installed stdio proxy command did not
complete initialization against that listener in the initial probe; no design
depends on assuming the two transports are interchangeable.

The native backend is the scheduling authority. The connector must verify its
queue, lifecycle and approval contracts before exposing unified mode. A custom
access layer is required only for concrete invariants native behavior cannot
enforce, such as a verified canonical binding or a terminal lease. Direct
shared-server attachment by itself proves neither recovery nor approval
ownership. Do not revive the existing headless auto-cancelling responder on a
shared conversation.

The backend and controller survive terminal detachment. A failed attach is not
permission to launch a second backend or resume the canonical history in a
private process. Losing the backend requires reconciliation before dispatch.

## Human, mail and peer inputs

Human input remains native. AMAP pointers enter the native per-thread queue;
incoming work waits while a human turn is active and the connector does not
automatically steer or interrupt it. Measure ordering against human queued
input, steering and cancellation before claiming global FIFO. Preserve native
operator controls where their reconciliation behavior has been verified.

Mail and peer admission retains the existing validated notice-pointer format,
source identity, message/reply correlation, reader framing and delivery journal.
Incoming body text must not be inserted as if the operator typed it. Sources
cannot supply model, permission, configuration or thread-selection overrides.

Journal AMAP intent before submission. Retain canonical binding/generation,
stable event identity, the exact pointer/hash, queue association when known,
and accepted/uncertain state. A queue timeout or a missing pending entry does
not authorize resubmission: inspect original input history too, and hold work
when evidence is insufficient. Treat human dispatches as recorded work for
thread recovery even when the AMAP ledger is empty. Prefer measured native
durable history over duplicating human input into another scheduler/journal.

The native CLI is the operator-facing proof of this mechanism. For controller
integration, prefer the corresponding `thread/queue/add` API so the journal can
record `clientUserMessageId` before sending and retain the returned submission
association. Do not parse human-readable CLI success text as the recovery
contract. Even a stable caller-provided ID does not make a repeated add safe.

Keep scanning admitted runtime notices at service startup and during operation;
the model does not become the notice watcher. Recover pending journal rows at
startup, rescan retained notices, and reconcile queue/history before any new
submission. A wake is a hint pointing at durable work. Frozen review records and
reply correlation can remain application conventions; a claim or reply is not
automatically a completed review, and neither replaces AMAP delivery outcomes.

Native queued items may be edited, deleted or reordered by an operator. A
missing or changed connector pointer must be reconciled explicitly; never
silently recreate cancelled work or apply a verdict to changed bytes. Requests,
claims, completed execution and outbound acknowledgements remain distinct.

## Policy and approvals

The operator explicitly chose the pane's permissions for all unified inputs.
Human, mail and peer turns therefore share the pane's sandbox and approval
policy, including danger-full-access with no approvals when that is the
operator-selected pane policy. Do not silently substitute a more restrictive
incoming-turn policy or a different receiver policy. A message body cannot
change those settings; permission changes require authenticated operator control.

Resolve the initial model and permission settings before starting the managed
thread. An explicit native model selection must agree with the service's
resolved model, or startup refuses and names the mismatch. Never let the pane
display one model while incoming work silently uses another configuration
source. Subsequent supported native operator changes must be observable to the
connector and recorded with their effective settings. Protected AMAP tools and
developer policy remain required, while approved native tools are retained.

All approval/user-input requests have one authoritative routing decision. The
current headless responder must not race a terminal response. The native queue
probe uses a passive controller observer, measures an offline approval hold,
and verifies native prompt recovery and decline without executing the command.
The experimental runtime adds generation binding and a single frontend lease,
with connection-bound approval response IDs.
With an eligible attached terminal, route the request to the operator; without
one, hold the work without granting permission or replaying the accepted turn. Approval
replies from the wrong session or backend generation are refused.

The first production mode permits one writable terminal client per instance.
A second attach is refused with an explicit existing-client message. It must
not silently become another input producer or an unverified read-only client.
Detachment releases the client lease while preserving the backend, queue and
conversation. Read-only additional views can be added after their protocol
behavior and request restrictions are independently verified.

Established history keeps old developer context. Refresh protected policy
through the measured supported API, while idle, before accepting further work.
Preparing an attachment must not start an unrecorded model turn or silently
reconfigure an active thread. Current policy must follow retained older context.
Developer guidance, MCP tool descriptions and activity metadata must all identify
the unified mode. Do not retain the separate mode's statement that incoming
work belongs to a different conversation after adopting a unified thread.

## Migration

Preserve the existing interactive history through an explicit adoption of its
exact ID after identity, workspace, policy and idle checks. Never merge the
contents of the old receiver and interactive histories automatically.

Keep the old delivery ledger and its finished events. Unresolved work stays
bound to its original thread until an operator resolves it. A switch of mode
or canonical thread is an audited transition, not an implicit consequence of
starting a terminal. A future reset/new-chat action must use the same transition
rules; unrestricted terminal thread creation cannot bypass them.

## Adapter launch and status contract

The runtime endpoint belongs in an instance-private state/runtime directory,
not the protected read-only payload, workspace, mail/peer lanes or a shared
host-access endpoint. The adapter supplies the private state location through
its generic environment contract; the connector publishes the exact endpoint
in its protected descriptor. The launcher consumes that descriptor and never
derives a socket path from a workspace name or chooses an ambient daemon.

In unified mode, both a fresh native launch and native `resume --last` mean
attach a terminal to the already registered canonical thread. Neither performs
native `--last` discovery or silently starts a different conversation. An
explicit selector naming another thread is refused. A new terminal client and
a reset of the canonical conversation are separate operations; a reset needs
the audited transition above. Initial adoption of pre-existing interactive
history is a dedicated operator action before ordinary attachment.

Startup and reconciliation expose a holding service rather than a successful
but disconnected pane. The adapter retains its normal hold record and bounded
launch wait. Readiness requires a live matching backend generation, the bound
canonical thread, matching configuration/build, refreshed policy, validated
protected tools, and reconciled scheduler state. An unavailable fact is unknown,
never a ready default.

Status must expose at least:

| Fact | Meaning |
| --- | --- |
| Mode | Explicit separate or unified mode |
| Backend generation | Identity of this owned backend execution |
| Canonical thread | Exact bound conversation ID |
| Attached client count | Current terminal leases, with at most one writer |
| Scheduler state | Idle, active, holding or reconciling |
| Active input source | Operator, mail or peer when known |
| Queue depth | Pending inputs, with source counts |
| Effective model and permissions | Resolved settings and their configuration source |
| Readiness checks | Named pass/fail/unknown evidence for the launch gate |

These facts belong in structured status, not log parsing. Missing fields remain
unknown to an adapter. The experimental runtime's schema version 1 encoding is
specified in [the runtime handoff](UNIFIED_RUNTIME.md#adapter-status-and-installation-handoff).

## Plan, implement, verify

1. **Live-thread feasibility — implemented as a probe.** Start one disposable
   backend, create one thread, attach the actual native terminal, observe a
   human turn from the controller, submit peer/mail pointer turns and observe
   their responses in that terminal. Detach and reattach without a new turn or
   duplicate history. Use a deterministic local provider, no personal auth,
   external inference or fleet traffic. Record exact build and proof limits.
2. **Native queue reliability — probe implemented.** Measure native busy/idle
   delivery, exact history retention, queue persistence across crashes, lost
   acknowledgements, duplicate submission/execution, cancellation, native queue
   controls and approvals. Steering, fork/reset, model/permission changes and
   every other unmeasured native control remain explicit integration gates.
   Use synthetic local inference and scratch-copy guard
   mutations. Record unsupported properties as gaps rather than replacing native
   behavior with an invented scheduler or claiming a production pass.
3. **Owned attachment and queue integration — implemented, pending review.** Implements the private endpoint,
   exact instance/thread/generation binding, terminal lease, bounded lifecycle
   handling and required observation. Reuse verified native queue behavior;
   journal AMAP intent before writes, associate queue and original turn inputs,
   distinguish queue admission from execution, and hold ambiguous work. Preserve
   native human interaction. Add only reconciliation and binding machinery that
   native behavior does not already supply. Verify stale, foreign and extra-client
   cases, backpressure and preserved settings. No feature activation yet.
4. **Approvals, recovery and adoption — partially implemented.** Sole-owner
   approval routing, offline holds and native decline are verified in the runtime
   probe. Explicit history adoption, policy transitions and audited native queue
   disposition remain. Verify no terminal/headless response race, no permission
   expansion, no replay and retained history for each added transition.
5. **Adapter handoff and pilot.** Supply a protected attach descriptor and
   generic launch contract. The adapter installs/configures the service and
   makes the private endpoint accessible inside its environment. Native
   `--no-daemon` cannot be combined with `--remote`; the mode's documented
   launcher must select the managed endpoint explicitly, without enabling an
   ambient shared daemon. Prepare adapter changes as unmerged PRs and preserve
   release pins. The operator applies reviewed changes and runs the pilot.

## Reproduce the first proof

Using the existing development interpreter:

```sh
python tests/integration/unified_probe.py --output /tmp/unified-probe.json
```

The fixture uses an actual native TUI and app-server over a private socket, but
a deterministic localhost server supplies synthetic Responses API events.
The human request is briefly held so active state can be observed. Controller
pointer turns are submitted only after completion, and both responses appear
in the same terminal. Reattachment preserves the exact backend history and
renders the final response.
Each original machine-input item must contain the complete expected pointer in
its accepted turn, exactly once. The probe snapshots the full ordered turn and
item data before detachment and requires exact equality after reattachment.
Equal turn counts or a surviving final marker alone do not establish retention.
Offline negative controls cover changed/missing lane and notice fields, wrong
turns, duplicates, echoed items, changed IDs/content/status and reordered history.

This proves transport, subscription, original pointer retention and native
rendering in the scripted sequence. It does not prove production arbitration,
approval routing, existing-thread adoption, an actual reader/submit round trip,
host isolation, crash recovery or a safe deployment. Those remain named gates.

Protocol references: [app-server transport and events](https://learn.chatgpt.com/docs/app-server)
and [native queue and CLI connection options](https://learn.chatgpt.com/docs/cli/reference).

## Reproduce native queue reliability

With the installed Codex build and development interpreter:

```sh
PYTHONPATH=src python tests/integration/native_queue_probe.py --output /tmp/native-queue-probe.json
python -m pytest tests/test_native_queue_probe.py tests/test_unified_probe.py
python tests/mutations/native_queue_probe.py
```

The probe creates a private disposable home/backend and synthetic local
provider. It exercises the actual native queue CLI, queue APIs and terminal,
including an actual keyboard decline in the disposable approval prompt.
It uses no user credentials, external inference or fleet messages. Its
test-only WebSocket client remains unsuitable as a production transport.
Offline controls and scratch-copy mutations reject foreign threads, wrong
client/turn IDs, edited input, duplicate originals, assistant echoes, wrong
execution status and an observer that answers an approval request.

This completes the named synthetic protocol investigation. The experimental
runtime implementation and its native service probe are described in
[UNIFIED_RUNTIME.md](UNIFIED_RUNTIME.md). Steps 4 and 5 remain rollout gates;
the commands above do not install unified mode.
