# Interactive Codex and supervised AMAP delivery

The connector provides two explicit surfaces. One supervisor owns the incoming
lanes and persistent app-server thread. An interactive Codex conversation can
send authorized messages and inspect the supervisor, but does not become another
receiver. A live activity view makes background work visible to an operator.
This model needs no particular isolation system, process supervisor or terminal
multiplexer. It does not mirror the supervisor transcript in the native TUI.

## Install and launch

Install the Python package, reader/submit executables, protected MCP TOML and
operator instructions as described in the README. All paths in the connector
configuration must be accessible where the interactive entry point runs. The
same Codex home/authentication is used; no credential copy or user-config edit
is performed. The existing state directory must already exist.

```sh
amap-codex --config /etc/amap-connector/connector.toml interactive
```

`interactive` first initializes the selected binary's app-server and reads its
effective configuration. A fresh launch creates or resumes no thread during
preflight; resume additionally prepares the selected CLI conversation as below.
The actual build must match `codex_version`. It then execs native Codex with:

- The protected `inbox`, `delegation` and `inbox_submit` MCP definitions.
- A required `amap_fleet` MCP server exposing only the read-only `activity` tool.
- Existing developer instructions followed by the operator policy and interactive
  guidance, including the separate-conversation boundary.
- The configured workspace and `--no-alt-screen`, retaining the startup guidance
  in terminal scrollback.

Model, authentication, approval policy, sandbox and network settings retain their
native configuration. Other native MCP registrations are retained. Existing
registrations named `inbox`, `delegation`, `inbox_submit` or `amap_fleet` are refused
instead of silently overwritten. Without a native `resume` request this starts
a new interactive conversation. The supervisor-owned thread is never a valid
interactive resume target.

`--binary` selects an installed native Codex executable. `--prompt` provides an
optional initial interactive prompt as a literal argument. No shell interpolation
is performed. A failure is reported; the connector never falls back to launching
an apparently connected but unconfigured session.

A generic adapter wrapper can pass its native argument vector directly:

```sh
amap-codex --config "$CONNECTOR_CONFIG" interactive --outbox-dir "$CONNECTOR_OUTBOX" -- codex --sandbox read-only -a on-request --no-daemon --model "$CODEX_MODEL" -c 'model_reasoning_effort="high"'
```

The first `--` ends connector options; the next argument is the actual Codex
binary, which is also used for preflight. Native option tokens keep their order
and contents, including sandbox, approval, model, reasoning, additional-directory
and feature options. A literal prompt must follow a second `--`. Use this form
instead of combining it with connector `--binary` or `--prompt`.

Native `-c`/`--config` and feature overrides also reach the app-server preflight.
The resulting MCP registry is checked for all four reserved AMAP names, and
effective native developer instructions are retained before the fleet policy and
interactive guidance are added. The connector appends its MCP/instruction
options after native options and before any literal prompt. It supplies the
configured cwd as a default; a native `--cd` overrides it and selects the cwd
used by preflight too. No shell parses this argument vector.

Local resume supports `resume --last` and `resume SESSION_UUID`:

```sh
amap-codex --config "$CONNECTOR_CONFIG" interactive --outbox-dir "$CONNECTOR_OUTBOX" -- codex resume --last --sandbox read-only -a on-request --no-daemon
```

`--last` selects the most recently created, non-archived CLI conversation in
the effective workspace, using a source filter and resolving it to an explicit
UUID before native launch. Editor/app-server sessions are excluded: Codex can
classify a supervisor's thread as an editor session. Explicit UUIDs must also
name CLI history in that workspace. Both the metadata lookup and subsequent
resume are checked; the bound supervisor ID is refused even if its reported
source is CLI. No missing target falls back to creating a conversation.

On Codex 0.164.0, changing `developer_instructions` on the command line does not
replace developer context already stored in an established conversation. After
verifying the selected target is idle and its protected four-server MCP registry
matches, the connector appends the current protected policy and guidance as a
developer item through `thread/inject_items`. It starts no model turn, preserves
the conversation history, journal and claims, and closes its app-server before
native Codex resumes the same explicit ID. Each explicit launch appends one
current policy item, including when the policy has changed; there is no stale
receipt-based shortcut. A failed or uncertain update refuses launch and is never
automatically retried. This path requires that build's supported injection API.

`interactive-args` remains read-only and refuses resume, because an argv preview
cannot prepare the current policy. Use `interactive` to execute resume. Picker,
named-session, cross-workspace, editor, `--all`, `--include-non-interactive`, fork,
remote and managed-worktree modes remain refused. Native `--profile`/`-p` is
also refused: the reviewed app-server preflight cannot load that CLI profile.
Unknown options fail explicitly. An adapter must report a refusal and must not
drop `resume`, silently start fresh or fall back to bare Codex.

For messaging, look up the intended recipient using `inbox_submit.peers`. Codex's
local subagent list does not discover fleet peers. Submit only within the
operator's authority and inspect `submit_result`; queued requests are inert,
and accepted sends do not establish that the peer completed a task. AMAP
delegation permissions can be directed. Do not predict a reverse send will be
accepted just because the other direction worked.

## See background work

Inside the interactive conversation, ask for `amap_fleet.activity`. To watch
without asking the model, use a separate terminal or visible pane:

```sh
amap-codex --config /etc/amap-connector/connector.toml activity --follow
```

The command emits a JSON metadata snapshot initially and when substantive state
changes. Each snapshot includes recent admitted incoming events, their turn IDs,
delivery/execution states, operator runs and the supervisor status. It exposes no
message bodies or model transcript. A finished delivery is not proof of a
completed business review, a verdict, or an outgoing message.

`--limit` bounds recent event/operator/result output from 1 to 100 (default 20).
`--interval` selects polling seconds from 0.1 to 60 (default 1). Unchanged
heartbeats do not flood the view. A heartbeat becomes `stale` after the larger
of ten seconds or three configured polling intervals. Missing timestamps are
`unknown`. These labels describe a recorded heartbeat, not router connectivity.
Before first startup, state is `not_started`; inspection creates no journal.

For bounded startup waits, use `readiness.state`, not heartbeat alone. `ready`
requires a bound thread, matching configuration/build, a fresh supervisor
snapshot with held claims and matching local claim-owner PID, no unresolved
dispatch, no operational error and no blocked requests. Every readiness check
is labelled `pass`, `fail` or `unknown`. Other states are `not_started`,
`not_ready` and `unknown`; none is permission to launch a second receiver.
Checks consider all unresolved operator runs even when display output is limited.
Only a valid empty blocked-request list establishes clear evidence; malformed
or missing fields remain unknown, including JSON null and other falsey values.
A recent snapshot from a previous process cannot establish the new owner is ready.
Controlled execution in another PID domain remains `unknown` for local ownership;
the adapter must establish its runtime evidence separately.

Readiness describes local startup, not idleness, router reachability, installed
payload revision or the adapter's own hold state. An adapter must also check its
configuration identity, hold marker and installed payload through its normal
verification. Inspection and the interactive launcher remain usable to diagnose
a supervisor that is not ready; neither performs recovery or clears an error.

For outbound visibility, supply the runtime's explicit outbox directory to both
the interactive launcher and monitor:

```sh
amap-codex --config /etc/amap-connector/connector.toml interactive --outbox-dir /amap/outbound/dropbox
amap-codex --config /etc/amap-connector/connector.toml activity --follow --outbox-dir /amap/outbound/dropbox
```

This directory must be the existing contract drop box. A missing `results/`
subdirectory is reported as `no_results_yet` and is not created by inspection;
the runtime may publish its first result after the interactive session starts.
This absence does not establish that no messages have been sent. A symlink or
non-directory at `results/` is still refused. The connector does not infer the
drop box from opaque MCP commands or adapter
environment variables. Without it, outbound observation is `not_configured`,
not an empty successful send history. The view lists queued request filenames
and bounded recent runtime results: request/message IDs, outcome, reason code
and timestamp. Invalid/unreadable results are labelled explicitly. Unknown
outcome strings remain strings; they are never converted into successful sends.
Request bodies, result detail and attachment contents are not displayed.

The monitor and MCP tool use SQLite read-only access, without opening the recovery
Journal object, acquiring claims, dispatching work or resuming the owned thread.
They can be used while the supervisor is running. An uncertain dispatch remains
uncertain until the existing recovery procedure resolves it. Inspection does not
authorize replay or transfer a supervisor task to the interactive conversation.

## Handoff to an isolation adapter

The connector owns these commands and their Codex-specific configuration. The
adapter installs them and supplies execution-environment paths; its generic
launcher can invoke `interactive` in place of bare Codex. Make the connector
configuration available before invoking it. No changes to router policy or
consumer-claim ownership are needed to install this surface.

For a source-payload installation without the package's console executable,
the equivalent entry point is the module. Supply the installed source directory,
configuration and existing outbox as explicit adapter inputs:

```sh
PYTHONPATH="$CONNECTOR_SRC" python3 -m amap_codex.cli --config "$CONNECTOR_CONFIG" interactive --outbox-dir "$CONNECTOR_OUTBOX"
PYTHONPATH="$CONNECTOR_SRC" python3 -m amap_codex.cli --config "$CONNECTOR_CONFIG" activity --follow --outbox-dir "$CONNECTOR_OUTBOX"
```

An adapter which assembles native argument vectors can instead run:

```sh
amap-codex --config /etc/amap-connector/connector.toml interactive-args
```

For fresh conversations it returns the complete argument vector as JSON after
configuration preflight. Resume requires `interactive` instead. Parse
that JSON and pass an argument array directly; do not join it into a shell command
or split it on whitespace. Index zero is the binary; following elements are
root-level native options, followed only by an optional `--` and literal prompt.
There is no native subcommand in this vector. To merge adapter options, pass
them to `interactive-args -- BINARY ...` before preflight rather than appending
them to an already generated vector. It contains developer instructions and local paths,
so retain it locally. Do not copy arbitrary live arguments into public evidence.
Do not persist these registrations in a shared Codex home: supervisor startup
rejects ambient registrations that collide with its trusted names.

Arrange for the documented activity command to be accessible as a visible
companion pane/terminal. Merely installing tools does not create that view. A
separate operator outside the execution environment needs the adapter's ordinary
execution/inspection facility; the connector adds no remote control endpoint.
For a host-side controller with agent-inaccessible state, that facility must
provide the view where the state is readable; do not expose private controller
state by weakening its mounts.

Acceptance checks after installation:

1. Initialize the interactive session and confirm all four AMAP servers expose
   their expected tools. A configuration listing alone is not startup proof.
2. Confirm the model finds fleet peers through `peers` and can inspect activity.
3. Send one operator-authorized test message, check its runtime result, and
   correlate a peer reply using the original message ID.
4. Observe admitted/active/finished or uncertain states and outbound results in
   the visible companion view while the native conversation remains separate.
5. Confirm inspection leaves claims, thread binding and uncertain work unchanged.

No fleet instance is changed by installing or testing this source checkout.
Activation belongs to the operator after review. Executing incoming work inside
the native interactive conversation itself would require a different, separately
verified ownership design.

The native settings used here are documented in the official
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
and [MCP guide](https://learn.chatgpt.com/docs/extend/mcp).

The opt-in negative-control inventory is reproducible with the existing test
interpreter: `python tests/mutations/interactive.py`. Each mutation runs against
an independent scratch copy and its named assertion; the working sources and
fleet state are never mutated. It requires the matching source/tests and pytest,
and does not require native Codex, network access or a logged-in account.
