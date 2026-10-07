# Compose deployment example

This is a mount and process-lifecycle example for one isolated Linux Codex
agent. It requires a deployment-built image with Codex CLI `0.160.1`, Python
3.11 or later, and the local `codex` executable. The host-side supervisor
launches a named one-off container through `launch-app-server.sh`. The
launcher attaches stdin/stdout for the JSONL protocol and explicitly stops
that container on shutdown; Compose then removes it. The sample has not been
run against Docker in this workspace, so process cleanup, bind-mount behavior,
model authentication, and actual isolation still need the live deployment
check.

Supply these host variables in a deployment-owned `deploy/.env` file (or
export them before launching). Keep the file mode `0600`; the repository
ignores this path:

| Variable | Required source |
| --- | --- |
| `AMAP_CODEX_IMAGE` | Pinned image containing Codex CLI 0.160.1, Python 3.11+, and an agent-owned `/codex-home` directory |
| `AMAP_AGENT_UID`, `AMAP_AGENT_GID` | Dedicated unprivileged container identity |
| `AMAP_CONNECTOR_ROOT` | Deployed connector root; only its `bin/` is mounted read-only |
| `AMAP_CODEX_CONFIG` | Deployed read-only `config/codex.example.toml` after replacing the model/self placeholders |
| `AMAP_OPERATOR_INSTRUCTIONS` | Deployed read-only `config/operator-instructions.md` |
| `AMAP_AGENT_WORKDIR` | Separate host directory writable by the agent identity |
| `AMAP_MAIL_NOTICE_DIR`, `AMAP_MAIL_MESSAGE_DIR` | This namespace's mail notice and message roots |
| `AMAP_PEER_NOTICE_DIR`, `AMAP_PEER_MESSAGE_DIR` | This namespace's peer notice and message roots |
| `AMAP_OUTBOUND_DROPBOX` | This namespace's drop box; `processed/` and `results/` must already exist |
| `AMAP_ROSTER_DIR` | Runtime roster directory |

The supervisor config uses host paths for its lane roots and claim files. Set
those to the same runtime-owned directories. Keep `state_dir` private to the
supervisor and outside the Compose mounts. The sample mounts inbound notice
trees (including sidecars), message trees, roster, Codex config, connector
binaries, and operator instructions read-only. Only `/work` and the outbound
drop-box root are writable. `processed/` and `results/` are nested read-only
bind mounts because the submit tool inspects those names to allocate request
IDs. The Codex home volume is separate persistent runtime state. Pre-create it
with ownership for `AMAP_AGENT_UID:AMAP_AGENT_GID`, then provision Codex
authentication there using the deployment's existing process. Do not put
mail or router credentials in it.

In `config/connector.example.toml`, use the deployed Compose file as the second
`launch_argv` element and a stable unique container name as the third.
An existing container with that name blocks
a new launch, including after a host launcher crash; inspect and stop it before
restarting. Use the same name throughout this instance's lifetime.
The Docker client and Compose project remain
host-side launcher dependencies. The container receives no Docker socket or
host control socket.
