# Running on Sandy

[amap-deploy-sandy](https://github.com/proofpoint/amap-deploy-sandy) is the
Sandy deployment adapter for this connector. It installs the connector as part
of the AMAP feature, renders its configuration, and verifies it. Its tutorial
section "Add a Codex agent" is the operator's procedure; this page says what
the connector does there and what has been demonstrated.

## Shape

- **One connector per sandbox.** A sandbox whose agents include `codex` and
  not `claude` is served by this connector; one with `claude` is served by the
  Claude connector. The two never run side by side.
- **The supervisor runs in the sandbox** as the feature's supervised entry,
  and launches `codex app-server` as its own child ([DESIGN.md](DESIGN.md)
  §1.1). Codex uses the sandbox's own `~/.codex` login. Nothing runs as root,
  nothing is added on the host, and no credential is copied.
- **The deployment writes the configuration** from Sandy's lane exports and
  session file: the instance id and address, the lanes, the stated Codex build
  (from `codex --version`), the model (from the agent's own
  `~/.codex/config.toml`, or Codex's default where it names none), the trusted
  MCP configuration and these operator instructions.
- **A hold is not an exit.** While Codex is not logged in, names no build, or
  the supervisor refuses to start, the deployment's relay writes a hold with
  the reason and retries. `verify` reports it.
- **The thread's tools are exactly the three AMAP servers.** Servers the
  agent's own Codex configuration registers, and the `codex_apps` server a
  ChatGPT login adds, are switched off for the supervisor's thread only; the
  agent's pane keeps them ([compatibility](../compatibility/README.md)).

## Demonstrated

On a live Sandy fleet with the real router, one Codex sandbox (codex-cli
0.161.0) and a Claude agent:

| Check | Result |
| --- | --- |
| Startup | Thread bound, both lanes claimed, nothing uncertain; an operator kickoff listed both inboxes and finished |
| Codex → Claude → Codex | The Codex agent delegated through `inbox_submit.submit` and saw it accepted; the Claude agent's reply was delivered back into the bound thread carrying `in_reply_to` of the original message |
| Claude → Codex → Claude | The Codex agent read the delegation, did the work it asked for, and replied; the router's notice of the reply carries `in_reply_to` of the request |
| Instruction change | `migrate` adopted new operator instructions on a kept journal and a new thread, with no notice delivered twice |

## Operating it

Inside the Codex sandbox, the deployment ships `status` and `kickoff` wrappers
beside the connector's source. For the rest of the CLI (`doctor`, `recover`,
`recover-operator`, `migrate`), run it against the rendered configuration:

```sh
PYTHONPATH=/opt/sandy/features/amap/codex/src python3 -m amap_codex.cli \
  --config /opt/sandy/feature-state/amap/codex/controller.toml status
```

[OPERATIONS.md](OPERATIONS.md) describes each command. Its host-service and
launcher-control sections apply to other deployments, not to this one.
