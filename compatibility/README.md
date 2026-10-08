# Codex compatibility evidence

First reviewed build: **codex-cli 0.160.1**, Linux aarch64, inspected October
6, 2026; 0.161.0 is reviewed below.

The installed binary generated the committed protocol subset with
`codex app-server generate-json-schema --out DIRECTORY`.
[schema-manifest.json](schema-manifest.json) records hashes. The client enables
the experimental API, completes `initialize` before `initialized`, and uses
the generated `developerInstructions` field on both start and resume.
The launcher's actual initialization user-agent version is checked against
the build the deployment states before starting a thread.

**Reviewed builds are information, not a gate.** `REVIEWED_CODEX_VERSIONS`
in `src/amap_codex/config.py` lists the builds these probes have covered. A
deployment runs the build it states whether or not it is listed, and
`status.json` reports `codex_reviewed`. On any build, delivery relies on
live checks: the launched app-server reports the stated build, each
thread's MCP registry is exactly the trusted one, and a protocol error fails
loudly. A move between builds keeps the journal and its thread and is
recorded in its audit table. Add a build to the list after re-running the
probes below against it.

## Live deployment (October 8, 2026; codex-cli 0.161.0)

One Codex agent served by this supervisor, running inside the agent's
isolation environment, with the reference router and another agent in the
fleet:

- Startup: thread bound, both lanes claimed, nothing uncertain; an operator
  kickoff listed both inboxes and finished.
- Codex → peer → Codex: the agent delegated through `inbox_submit.submit` and
  saw it accepted; the peer's reply was delivered into the bound thread with
  `in_reply_to` of the original message.
- Peer → Codex → peer: the agent read the delegation, did the work it asked
  for, and replied; the router's notice of the reply carries `in_reply_to` of
  the request.
- `migrate` adopted changed operator instructions on the kept journal and a
  new thread, and no notice was delivered twice.

The deployment found the `codex_apps`, fresh-thread history and instruction
behaviors recorded below.

## Shared authentication and process recovery (0.161.0)

[shared-auth-refresh-0.161.0.json](shared-auth-refresh-0.161.0.json) records two
real app-servers using one existing managed ChatGPT login. Both requested a
token refresh concurrently, the authentication refresh metadata changed,
and both still reported the same account. No credentials were copied and the
probe performed no inference or submission.

[shared-auth-interactive-supervisor-0.161.0.json](shared-auth-interactive-supervisor-0.161.0.json)
adds real actor evidence: an interactive TUI with its own app-server and an
actual in-environment supervisor stayed running across that forced rotation.
Both completed synthetic marker turns before and afterward; their persisted
agent messages, not input echoes, were inspected. Neither used tools or
submitted outbound work. This demonstrates coexistence across managed token
rotation; it did not induce an automatic expiry-driven refresh in the TUI.

[process-recovery-0.161.0.json](process-recovery-0.161.0.json) records a real
supervisor kickoff that entered a bounded workspace command. The test killed
its app-server group and intentionally exited its controller, retaining crash
records, claims and journal. A new supervisor resumed the same thread, matched
the original input and recovered its turn as interrupted. There was one
workspace-command execution and no new dispatch across two idle cycles.
This is **process-crash evidence within isolation**, not an entire sandbox
restart or router test.

The first workspace-command attempt under an inner `workspace-write` policy
was denied because this environment cannot create a nested sandbox namespace;
it was not a recovery pass. The successful disposable probe explicitly relied
on outer isolation with `danger-full-access`. No deployed policy was changed.
See [LIVE-CHECKS.md](../docs/LIVE-CHECKS.md) for reproduction and the external
restart procedure. Full isolation-environment replacement remains pending
operator-host control and a dedicated test target.

## codex-cli 0.161.0 (reviewed October 7, 2026; Linux aarch64)

Probes that need no login, each also re-run on 0.160.1 for comparison:

- **Schema subset.** `generate-json-schema` output differs from the
  committed 0.160.1 subset in four files: `v2/ThreadReadResponse`,
  `v2/TurnCompletedNotification`, `v2/TurnStartResponse` and
  `v2/TurnStartParams`. In the first three, `CodexErrorInfo` changes from
  `oneOf` to `anyOf` with one added open variant (`string` or `object`), for
  error codes a later build adds. The connector never reads that field. The
  fourth changes one description string. The committed subset stays 0.160.1's.
- **Three-server registry.** [mcp-registry-probe-0.161.0.json](mcp-registry-probe-0.161.0.json):
  a thread is created and its effective registry is exactly the three
  trusted servers and their tool sets, with the agent's own two servers
  listed and switched off.
- **MCP environment.** Without `env_vars` a stdio server receives only `HOME`
  and `PATH`, and the required servers fail to start; with it, exactly the
  listed variables arrive. Same as 0.160.1.
- **No model.** `thread/start` without `model` and with an empty
  `CODEX_HOME` is accepted and reports `gpt-6.1-sol`. Same as 0.160.1.
- **Login status.** Unauthenticated, `codex login status` prints `Not
  logged in` and exits 1. Same as 0.160.1.

Not re-run: the authenticated live probe (an actual turn with an MCP read).

[live-host-probe.json](live-host-probe.json) records a real authenticated
host-side run using the account's selected `gpt-6.1-sol`, low reasoning effort,
one synthetic mail body, and one explicitly permitted local MCP read tool:

- Standalone tool output was accepted with `input: []` and the fixed
  `amap_connector.amap_notice` source.
- The model called `inbox.read_message`, fetched the synthetic body, returned
  its marker, and completed the turn.
- `thread/read` exposed the original connector input as `functionCallOutput`.
- A fresh app-server resumed the same persistent thread and exposed that input.

The first host probe used the implicit MCP approval policy with headless
`approvalPolicy: never`. Delivery was accepted, but the MCP read was denied;
turn completion alone did not prove the message had been fetched. The corrected
probe and deployment example explicitly permit the exact AMAP tool allowlists
using `default_tools_approval_mode: approve`. This permission covers local reads
and inert submissions; the runtime retains outbound sending authority.

Reproduce the host probe from a source checkout with authenticated Codex:

```sh
PYTHONPATH=src python tests/integration/live_probe.py \
  --model MODEL_AVAILABLE_TO_YOUR_ACCOUNT \
  --output compatibility/live-host-probe.json
```

The fake-server suite verifies request correlation, early completion,
timeout/partial-boundary uncertainty, subprocess death, generic RPC errors,
approval cancellation, user-input interruption, conservative history matching,
serial turns, claim contention, restart, and cleanup. Response envelopes are
checked against generated schemas. A 25 MiB binary attachment test measures
the imported reader's path/metadata output; bytes are not serialized over this
control interface. Frames default to 32 MiB, above the reader's 8 MiB published
JSON bound and 64 KiB inline attachment content.

## Required deployment verification

The in-environment deployment demonstrated the startup, bidirectional
delegation/reply and instruction-migration results recorded above. Those
results do not close every gate or validate the optional host-side/Compose
model. The following separates measured behavior from additional evidence
needed for a deployment:

| Gate | Evidence and remaining scope |
| --- | --- |
| In-environment launcher | Startup and turns demonstrated on 0.161.0; actual sandbox-restart-mid-turn evidence remains pending |
| Optional remote launcher | Host-side/Compose orphan detection and exact remote descendant cleanup have not been demonstrated live |
| OS isolation | Deployment-owned write/rename/chmod/symlink and cross-namespace/credential/socket checks require explicit evidence; the in-environment model permits access to the agent's own journal |
| MCP deployment | Effective three-server/tool registry measured; attachment sidecars, nested result/processed views and atomic staging need deployment-specific evidence |
| Trusted instructions | Instruction migration to a new thread on the kept journal demonstrated; immutable workflow configuration remains a deployment responsibility |
| Live headless requests | Actual approval and user-input protocol requests resolve visibly without permission expansion |
| Router round trip | Delegation and correlated replies in both directions demonstrated; this evidence does not establish every attachment or reply-loop case |
| Outcomes | Router agrees to `outbound/ext/codex/outcomes`, vocabulary and crash publication semantics |
| Recovery demo | Migration and real app-server/controller crash recovery retained state without duplicate dispatch; full sandbox replacement during a turn remains unproven live |
| Shared Codex authentication | Concurrent managed refresh and real TUI/supervisor turns across rotation passed; automatic expiry-driven TUI refresh was not induced |
| Runtime contract | Canonical claim paths/PID namespace, publish ordering, retention window, authenticated model egress |

The wake-up fallback is implemented and fixture-tested but has no live proof;
keep `delivery_mode = "tool_output"` for this build. A failed accepted turn is
an execution diagnostic and is never automatically replayed. Missing original
input in partial history never establishes non-delivery.

Protocol and configuration sources: [official app-server interface](https://learn.chatgpt.com/docs/app-server)
and [official MCP configuration](https://learn.chatgpt.com/docs/extend/mcp).

The [three-server startup probe](mcp-registry-probe.json) uses the pinned actual
app-server and all three vendored MCP binaries. It creates a thread and validates
the effective server names and exact tool sets without any inference or submission.
Reproduce with `python tests/integration/mcp_registry_probe.py --model MODEL
--output compatibility/mcp-registry-probe.json`. Thread overrides are sent on
start/resume and the actual registry is checked: CLI TOML map overrides merge
ambient MCP registrations rather than necessarily replacing them.

**The agent's own MCP servers are switched off for the thread.** Measured on
0.160.1 and 0.161.0: servers the agent's Codex configuration registers join a
thread beside the trusted three. Before each start or resume, the supervisor
reads them with `config/read` (cwd = the thread's) and adds `enabled = false`
for each to the thread's override. Such a server stays listed with no tools,
and the check requires exactly that. A registration `config/read` does not
report, or one named like a trusted server, is refused. The probe gives its
Codex home two such servers, one enabled and one already disabled. This probe
is host protocol evidence and does not close the live deployment gates.

**Codex's own `codex_apps` server is kept out of the thread.** Measured on
0.160.1 and 0.161.0: under a ChatGPT login, the `apps` feature (stable, on by
default) adds a `codex_apps` server to every thread, and `config/read` does
not report it. The thread's override sets `features.apps = false`, which
removes it. [mcp-registry-probe-chatgpt-0.161.0.json](mcp-registry-probe-chatgpt-0.161.0.json)
is the probe run with `--chatgpt-login`: a synthetic login with every endpoint
and proxy pointed at a closed local port, so nothing leaves the machine.
Without the override, the same run is refused with `unexpected ['codex_apps']`.

**A thread the app-server started has no listable history until its first
turn.** Measured on 0.160.1 and 0.161.0: `thread/read` with `includeTurns`
on such a thread is refused (-32601, "list_turns is not supported yet"); the
same thread resumed in a new app-server reads, with or without a turn. The
supervisor reads history once, at startup, so a thread it has just started
is read as empty without the call. On 0.161.0 the refusal also lasts a
moment past the first `turn/start`; nothing reads history there.

**A thread keeps the developer instructions it started with.** Measured on
0.160.1 and 0.161.0: `developerInstructions` sent on `thread/resume` are not
recorded in the thread, and the turn after the resume carries the original
ones. A change of operator instructions therefore needs a new thread, which
`migrate` provides (docs/OPERATIONS.md).
