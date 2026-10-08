# AMAP Codex Connector

This is a local reference connector that wakes one dedicated Codex app-server
thread from validated AMAP mail or peer notices. The isolated agent reads
from local MCP servers and can create outbound requests in the runtime's
drop box. The runtime remains responsible for policy and sending.

This is a reference implementation, and Codex app-server is experimental.
It has run in a live AMAP fleet with the reference router, delegating to and
answering other agents in both directions.
The reviewed Codex builds are `codex-cli 0.160.1` and `0.161.0`; any build
runs, and delivery relies on live checks. The Compose example is provided for
review and has not been run as a live deployment. See
[compatibility/README.md](compatibility/README.md) for the evidence.

## Install

Use Python 3.11 or later and the connector source tree. Install the package
into an isolated environment, then deploy the `bin/` MCP executables and
operator instructions at paths visible to the agent container:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install .
codex --version
```

Set the supervisor's `codex_version` to what `codex --version` prints; the
launched app-server must report that build. Provision Codex authentication through the deployment's
existing mechanism. The agent needs Codex model access; it must not receive
mail-provider or router credentials.

Copy `config/connector.example.toml`, `config/codex.example.toml`, and
`config/operator-instructions.md` to deployment-owned locations. Replace each
`REPLACE_*` value with operator-selected settings. The host config must use
the actual absolute host paths and existing namespace directories. The Codex
config must use paths inside the sandbox. Keep the supervisor `state_dir`
private (mode `0700`) and outside agent mounts. Create it before running the
read-only `doctor` or `status` commands. Do not point separate mailboxes at one
journal or claim file.

The Compose example is in [deploy/README.md](deploy/README.md). It requires
explicit host directory variables and a deployment-built image. Docker is a
host-side launcher dependency; the container does not receive a Docker socket.
Use another deployment-owned launcher if the pilot does not use Docker, and
make sure stopping its host process terminates the actual app-server.

## Start and inspect

The CLI uses the host configuration explicitly:

```sh
amap-codex --config /etc/amap-connector/connector.toml doctor
amap-codex --config /etc/amap-connector/connector.toml doctor --probe
amap-codex --config /etc/amap-connector/connector.toml status
amap-codex --config /etc/amap-connector/connector.toml run
```

`doctor` checks local configuration and paths without starting the app-server
or delivering a notice. `doctor --probe` additionally launches the configured
stdio app-server, performs initialization and the bound-thread start/resume
check, then stops it; it does not scan or deliver notices. Use the exact
container image, mount layout, authentication, and launcher intended for the
pilot when probing. Keep `run` in the foreground under the deployment's
service manager so signals reach the supervisor and launcher.

The supervisor acquires the existing lane claims before accepting work,
binds one persistent thread, and serializes delivery. A repeated scan does not
re-deliver an already accepted event. New events wait while the thread is
active. If dispatch may have reached Codex but acceptance cannot be proved,
the event becomes uncertain and automatic dispatch pauses for the instance.

See [docs/OPERATIONS.md](docs/OPERATIONS.md) before using recovery commands.

A deployment adapter renders the configuration, mounts and launcher for its
isolation system, and decides where the supervisor runs
([docs/DESIGN.md](docs/DESIGN.md) §1). The connector knows no particular
adapter.

## Trust and sending

The operator instructions ([config/operator-instructions.md](config/operator-instructions.md))
are the AMAP fleet's inbox policy. Mail and delegations are requests meant to
be acted on, within the authority the agent already has, and a reply to the
sender needs no permission. A peer's identity is runtime-asserted; what it
quotes, forwards or attaches is untrusted data. Where the router's guarantee
does not reach (credentials, network, irreversible or off-host changes), the
agent does not act and tells the sender it needs the operator.

Peer replies use the notice's validated `peer_from` as `to` and
`peer_message_id` as `in_reply_to`. The local `notice_id` only locates the
receiver's spool file; these IDs are not interchangeable. Results are consumed
into ongoing work without automatic acknowledgments.

`inbox_submit.submit` writes an inert request; it does not send. The agent
must call `submit_result` and report a send only when the runtime says the
outcome is `accepted`. A Codex final response is a local execution transcript,
not a mail draft. The connector adds no AV, DLP, or prompt-injection scanning.

## Development and CI

GitHub Actions runs pytest on Python 3.11 and 3.13 and validates the vendored
AMAP fixture subset. The source-import tests retain their upstream attribution
and cover reader, submit, claims, and attachment behavior. Live Codex and
router acceptance checks are separate deployment tests and are not inferred
from CI.

Codex MCP and sandbox settings in `config/codex.example.toml` use the Codex
CLI's `mcp_servers`, `enabled_tools`, `required`, `workspace-write`,
and sandbox network settings. See the official [MCP configuration guide](https://developers.openai.com/codex/mcp)
and [configuration reference](https://developers.openai.com/codex/config-reference).
The exact AMAP tool allowlists have `default_tools_approval_mode = "approve"`;
this provisions permission for local reads and inert submissions. The runtime
still authorizes and sends each outbound request. Additional approvals are canceled.

Run deterministic checks with `python -m pip install -e '.[test]'`, then
`python -m pytest` and `python vendor/amap-spec/fixtures/validate.py`.

## Current limitations

- One namespace, one supervisor, and one persistent Codex thread per instance.
- The Compose example and launcher control have not been run live.
- The `outbound/ext/codex/outcomes` path is consumed by a router configured
  for it (amap-router-local's `connector_outcome_ids`).
- The connector has no UI for approvals, interrupted work, or recovery.
- This pilot does not provide exactly-once model execution or exactly-once
  outbound effects, a cloud service, remote MCP, or native MCP Events.

[Launcher control v1](docs/contracts/LAUNCHER-CONTROL.md) and operator kickoffs
are optional; direct local launch remains supported.
