# Unified conversation

Status: engineering in progress. The isolated native-TUI feasibility probe
passes on Codex 0.164.0. No production command, deployment activation or
automatic migration is provided by this change.

## Decision

The operator and the mail/peer lanes use one canonical conversation per
connector instance. The connector becomes the delivery controller for that
conversation, rather than maintaining a second model conversation. A terminal
is an attachable client of the durable backend; closing it does not transfer
claims or cancel incoming work.

Keep the existing headless/separate mode supported. Unified mode will be an
explicit configuration choice, with its own startup and migration checks. It
must remain independent of any deployment adapter or terminal multiplexer.

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
probe includes a deliberately limited, test-only client. This is not a
production transport implementation. The installed stdio proxy command did not
complete initialization against that listener in the initial probe; no design
depends on assuming the two transports are interchangeable.

Before production exposure, a connector-owned access layer must mediate every
state-changing terminal request. A direct shared-server attachment proves
rendering feasibility but does not provide input arbitration or approval
ownership. Read-only observation and direct backend mutation are not equivalent.

The backend and controller survive terminal detachment. A failed attach is not
permission to launch a second backend or resume the canonical history in a
private process. Losing the backend requires reconciliation before dispatch.

## Human, mail and peer inputs

All inputs enter one controller scheduler. Default ordering is FIFO with one
active turn. Incoming work waits while a human turn is active; the connector
does not automatically steer or interrupt it. Explicit operator steering or
stopping needs an audited control path and its own supported protocol contract.

Mail and peer admission retains the existing validated notice-pointer format,
source identity, message/reply correlation, reader framing and delivery journal.
Incoming body text must not be inserted as if the operator typed it. Sources
cannot supply model, permission, configuration or thread-selection overrides.

Human input also needs a durable intent record before transport submission.
The record must identify its source and canonical generation, retain a request
hash, and correlate accepted/uncertain submission with original history.
Codex 0.164.0 exposes `clientUserMessageId`; its history/reconnect behavior must
be measured before using it as reconciliation evidence. Do not treat a timed-out
terminal RPC as proof that a human turn was never submitted.

The controller must coordinate native `turn/start`, queued inputs, steering,
interrupts and other turn-producing methods. A queue class in isolation is not
proof that native clients cannot bypass it. Unknown mutations are refused until
their ownership, recovery and permission behavior has been implemented.

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
source. Subsequent supported operator changes go through the controller and
are recorded with their effective settings. Protected AMAP tools and developer
policy remain required, while approved native tools are retained.

All approval/user-input requests have one authoritative routing decision. The
current headless responder must not race a terminal response. With an eligible
attached terminal, route the request to the operator; without one, hold the
work without granting permission or replaying the accepted turn. Approval
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
unknown to an adapter. Field encoding/versioning will be fixed with the runtime
implementation; this document does not claim these fields already exist.

## Plan, implement, verify

1. **Live-thread feasibility — implemented as a probe.** Start one disposable
   backend, create one thread, attach the actual native terminal, observe a
   human turn from the controller, submit peer/mail pointer turns and observe
   their responses in that terminal. Detach and reattach without a new turn or
   duplicate history. Use a deterministic local provider, no personal auth,
   external inference or fleet traffic. Record exact build and proof limits.
2. **Connection and request ownership.** Implement the private endpoint and
   terminal access layer, exact instance/thread/generation binding, lifecycle
   cleanup and bounded event forwarding. Verify stale, foreign and extra-client
   cases, backpressure and preserved per-thread settings. Test every guard on
   scratch copies. No feature activation yet.
3. **Durable unified scheduling.** Journal human intents and AMAP admission
   before writes, serialize native and incoming requests, reconcile by original
   input/turn identity, and hold ambiguous work. Verify real native busy-input,
   queue, cancellation and disconnect semantics rather than inventing response
   shapes the native UI cannot consume.
4. **Approvals and adoption.** Add sole-owner approval routing, offline holds,
   policy refresh and explicit history adoption. Verify no terminal/headless
   response race, no permission expansion, no replay and retained history.
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
in the same terminal. Reattachment renders the same three-turn history.

This proves transport, subscription, original pointer retention and native
rendering in the scripted sequence. It does not prove production arbitration,
approval routing, existing-thread adoption, an actual reader/submit round trip,
host isolation, crash recovery or a safe deployment. Those remain named gates.

Protocol references: [app-server transport and events](https://learn.chatgpt.com/docs/app-server)
and [native CLI connection options](https://learn.chatgpt.com/docs/developer-commands).
