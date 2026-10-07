# Sandy AMAP deployment handoff

Implementation status: engineering changes are prepared and locally verified.
See [verification and PRs](../ENGINEERING-VERIFICATION.md) and the
[operator runbook](../OPERATOR-RUNBOOK.md). The requirements below remain the
review/acceptance contract; host-only gates are pending, and nothing is auto merged.


Add one Codex-only workspace to the existing fleet and execute
[the rollout](../ROLLOUT.md) after the connector, router and Sandy capability
gates pass. Keep all Sandy-specific discovery and service code in this
repository. Preserve the current fleet's policy, addresses and router state.

## Requested changes

1. Pin `amap-connector-codex` as an additional dependency while retaining the
   Claude pin. Extend installation and verification by declared connector kind,
   with a single connector assignment per AMAP namespace. Reject a mixed
   Claude/Codex delivery assignment for the same spool during this pilot.
2. Retain one common `amap` feature instance tree and discovery file. Select its
   lane/roster/payload mounts for both agent kinds. Move the existing Claude relay
   activation into a Claude-only feature entry, such as `amap-claude`, with its
   own immutable relay payload. Its entry can consume the common feature's trusted
   lane exports. Update introspection checks to follow the declared feature
   entries; do not assume every selected AMAP sandbox has the old relay entry.
3. Preview and migrate existing Claude selection/entry state through normal
   install/relaunch. Verify the old relay is stopped before a replacement starts.
   Preserve spools, submissions, router state and existing Claude prompt/MCP
   behavior. A Codex sandbox must receive no Claude delivery entry.
4. Install Codex read/submit tools and operator workflow read-only. Render host
   supervisor TOML with absolute host paths, address, model, working directory,
   pinned Codex version, private state, canonical claims and explicit outcome
   path. Render trusted Codex MCP registration using actual sandbox paths or
   wrappers consuming lane exports. Do not copy Claude JSON interpolation into
   Codex TOML. Keep the exact three-server allowlist and inert submit behavior.
5. Implement launch and control helpers against the connector's published
   generic lifecycle schema. Use Sandy's actual sandbox/container identity,
   host uid/gid, `HOME`, credential provisioning and no-TTY pipes. Respect existing
   egress and recreation policy. Identify and stop the exact owned app-server and
   children; reconcile an orphan before launch. Do not install a Docker socket,
   host journal or service-control capability inside the agent.
6. Implement the canonical host guard for the Codex pilot and on behalf of the
   participating Claude relay, using the connector ownership contract. Keep it
   outside agent mounts. Adopt a verified singleton Claude execution or stop it
   before migration. Persist runtime identity so a host guard process crash does
   not make a surviving container execution reclaimable. Keep legacy local claims
   as extra protection; all supported starts for pilot namespaces must honor the
   host guard. Verify bypass attempts fail rather than relying on convention.
7. Render an operator host service per Codex instance, with preview/apply and
   ownership-aware restart: launchd on macOS and the site's equivalent on Linux.
   Coordinate stop/restart with Sandy container recreation; retain the host
   journal and per-sandbox Codex auth/history. Follow schema capabilities instead
   of fragile version guesses.
8. Render router `connector_outcome_ids=["claude-code","codex"]` once supported,
   alongside the reviewed fleet graph. Configure Codex output under the instance's
   `outbox/ext/codex/outcomes`. Keep replay disabled until the router's idempotence
   agreement is tested. Add the approved `C → B` and `B → C` edges without changing
   existing old-to-old edges or quietly broadening Codex's admission.
9. Add protected outbox child mounts through Sandy's new generic capability.
   If unsupported, fail preflight. Verify runtime-owned `results` and `processed`
   cannot be modified as the agent uid. For Codex, expose a submission projection
   that keeps the real host `outbox/ext/codex/outcomes` outside the agent view,
   such as shadowing `ext` with a read-only empty payload tree. Use generic
   selection/profile capabilities for this difference; preserve the existing
   Claude relay's writable outcome path. Verify no writable alias exposes the
   host outcomes. Retain notice-side attachment mounts.

## Host rollout procedure

Inventory installed revisions/schema, Docker context, chosen workspace/slugs,
fleet domain, canonical claim locations and service manager. Let Sandy create
the sandbox/lanes; retain router first-sight state and wait for the new instance
snapshot before sending. Start with one dedicated Codex instance and one known
Claude target. Launch the host controller after isolation/startup verification.

Use the connector's opt-in operator harness for Codex to Claude to Codex.
Use an operator instruction in the existing Claude session for Claude to Codex
to Claude. Each sends a uniquely labeled synthetic UTF-8 attachment and returns
its marker/hash, with one request and one correlated reply. Use the gated MCP
submit tools at both ends and check actual runtime send results. Never satisfy
the test by writing a peer notice directly, sending from the host, or creating
a second app-server thread. Do not add a nonexistent `task_id` submit argument.

## Verification and acceptance

Extend `verify` to report actual assignment, host service, canonical claim,
isolated execution, bound thread, pending/uncertain counts, mount integrity,
MCP configuration, router admission and outcome support. UNKNOWN remains a
failure to establish readiness. Probe actual sandbox state and router ledger
results; a generated file or healthy local PID alone does not prove delivery.

The existing Claude fleet must verify healthy after migration. Both round trips
must pass with distinct notice/message IDs, accepted send results, matching
attachment hashes and no acknowledgment loop. Supervisor restart, remote orphan,
abrupt dispatch and Sandy recreation must preserve the recorded thread and
prevent replay. Archive the evidence described in the rollout and document the
precise installed commands and minimal rollback for the new instance.

Deliver changes as independently reviewable dependency/install, assignment,
launcher/ownership, service and verification steps. Keep connector-core imports
free of Sandy and preserve deployment dry-run behavior.
