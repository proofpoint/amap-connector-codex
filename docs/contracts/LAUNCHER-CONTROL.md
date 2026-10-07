# Launcher control v1

The generic supervisor supports direct local process groups by default. An
external isolation adapter opts in through `launcher_control_argv`,
`deployment_id` and `owner_domain`. No Sandy or Docker dependency is required.
The request and response JSON schemas in this directory define the wire shape.

Launch the configured `launch_argv` as a foreground process with JSONL stdio,
no PTY, diagnostics on stderr. The host supplies `AMAP_DEPLOYMENT_ID` and a fresh
`AMAP_EXECUTION_ID`. Before any remote spawn, the adapter commits a durable
binding to the exact runtime identity. Execution IDs must never be reused.

For control, the supervisor appends exactly `inspect` or `stop` to the configured
argument vector. One JSON object arrives on stdin; return one JSON object on
stdout and exit zero. Responses must echo both identities and integer version 1.
No extra fields are allowed. Stdout and stderr are each limited to 65,536 bytes.
The configurable wall deadline defaults to 10 seconds. The Sandy adapter uses
15 seconds for a root helper whose termination check is bounded to 9 seconds.
The supervisor kills and reaps an expired helper process group. Nonzero exit,
wrong identity, malformed/oversized output and timeout all mean unknown.

```json
{"version":1,"deployment_id":"pilot:one","execution_id":"execution-unique"}
```

```json
{"version":1,"deployment_id":"pilot:one","execution_id":"execution-unique","state":"stopped"}
```

`running` means the bound execution is alive. `stopped` must prove that execution
and all its descendants are gone, or durably revoke that ID before a delayed
launcher can spawn it. An absent record alone is insufficient. `unknown` holds
ownership and blocks a replacement, including after a host PID dies. A lost stop
response can be resolved by later inspection. Never inspect a newly discovered
container as a substitute for the originally bound one.

Canonical guards live on the host outside agent and router mounts. Every
supported controller for a namespace must contend on both lane guards. Legacy
claims require a coordinated stop and positive termination before migration;
container PID values cannot be reinterpreted as host PID values. Do not delete
ambiguous records to unblock a launch. Guard-command holds the same guards for
another connector without creating a Codex model or thread.

```bash
amap-codex --config /absolute/host/controller.toml cleanup --stop
amap-codex --config /absolute/host/controller.toml status
```

Stop the host service first. Cleanup acquires the private controller lock and
checks the bound deployment/owner domain before invoking stop. It retains the
journal and durable claims for normal recovery. A missing process record means
there is no recorded controller execution to clean; inspect retained guards
rather than interpreting that CLI error as a stopped remote execution.

The portable fixtures in tests/test_rollout_core.py use an independent Python
launcher/control helper. Sandy managed execution is a separate adapter test.
