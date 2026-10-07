# Rollout engineering verification

Rollout is blocked pending the review follow-up on [Sandy #444](https://github.com/rappdw/sandy/pull/444)
and the lifecycle design in [Sandy #446](https://github.com/rappdw/sandy/issues/446).
#444 now covers protected submounts only. Protected sources must be outside
**every** writable mount source, including their parent, and destinations must
already exist. The deployment draft must revise its current result/processed
source layout and replace its dependency on Sandy's removed `managed_exec.py`.
That helper cannot write under `/run` in Sandy's read-only container root;
only `/tmp` and `/home/sandy` are tmpfs. The operator apply/provision/service and
round-trip commands below are a draft, not authorization or readiness to run them.
No fleet changes have been applied.


Integration PRs are drafts with manual merge only. No fleet manifests, host
services, router state or real sandbox membership have been changed by this work.
The new canonical connector repository will be proofpoint/amap-connector-codex;
its publication/PR is pending creation. Core changes are reviewable in this source
workspace and the built wheel; no Git revision is invented for them.

| Repository | Review |
| --- | --- |
| Sandy | [PR #444](https://github.com/rappdw/sandy/pull/444) |
| amap-router-local | [PR #1](https://github.com/proofpoint/amap-router-local/pull/1) |
| amap-deploy-sandy | [PR #30](https://github.com/proofpoint/amap-deploy-sandy/pull/30) |

Each change followed plan → implement → verify. State/lifecycle decisions were
reviewed directly with deterministic crash fixtures; rendering/docs used bounded
changes and existing regressions. No additional model inference was used for
engineering verification. The startup registry probe invokes the real binary
without a turn. Live START1/R1/R2 inference is reserved for the host rollout.

| Step and plan | Implemented behavior | Verification |
| --- | --- | --- |
| Execution contract: exact IDs, bounded I/O, positive cleanup | Generic launcher-control v1, persisted pre-spawn binding, unknown holds, cleanup CLI | Non-Sandy control helper fixtures: timeout, malformed/oversized output, wrong ID, remote-alive and unknown cleanup |
| Ownership: one host guard for each pilot lane | Owner-domain/execution-bound canonical guards, guard-command for Claude, conservative legacy migration | Foreign/container PID claims held; surviving remote execution blocks reclaim; partial startup/shutdown tests |
| Trusted startup: no ambient tools | Optional protected config, thread start/resume overrides, effective three-server/tool registry checks | Actual Codex 0.160.1 and all three vendored MCP binaries passed; no inference/submissions; config allowlist/hash tests |
| Privileged kickoff: one queue and persistent thread | Opt-in private queue, durable operator_runs states, serialization with notices, audited hold/handled | Same-ID restart dedup, committed-dispatch uncertainty, no notice artifacts/outcomes for operator work |
| Isolation primitives: protected children | Generic profile-selected read-only submounts; managed execution deferred to #446 | Review found a source-alias bug and invalid helper filesystem assumptions; #444 corrects the source alias with numbered launch and mutation checks |
| Router: explicit outcomes, default compatibility | connector_outcome_ids, validated opaque segments, shared scan budget and semantic dedup | 685 tests + 184 subtests; Python 3.9/3.13 CI passed |
| Deployment: preserve fleet and review actual paths | Read-only inventory/preview, source/preimage hashes, retained router state, explicit C↔B edges, services, probes, round trips, scoped rollback | 550 tests + 311 subtests locally; source drift, binding revocation, exact TOML, runbook and rollback checks; 3.9 legacy compatibility |

Core regression suite: **164 tests and 8 subtests passed**. Imported fixture
validator: **44 fixtures passed**. The wheel builds successfully. Two imported
DeprecationWarnings remain from the pre-existing module loader.

The earlier Sandy pytest/helper results and CI run do **not** establish rollout
readiness: review identified a writable-source alias and a helper writing to a
read-only root. Those tests and the helper are removed from #444. Replacement
verification is numbered `test/run-tests.sh` §200, with no pip or image pulls,
actual launch argv captured behind runtime stubs, and mutations on scratch Sandy
copies. On corrected head `e98b55c`, all seven §200 checks pass locally: 43 refusal
fixtures, five positive shapes plus profile omission, Node/jq byte parity,
positive/refused full-script launches behind runtime stubs, and 37 detected
scratch mutations. Bash syntax, Bash 3.2 lint (22 files), and generated-artifact
checks pass. [Full CI on that head](https://github.com/rappdw/sandy/actions/runs/37543756977)
passed both jobs, with all **2,831 tests passed**, including §200. This verifies
the corrected protected-submount implementation; remote process lifecycle and
actual-host rollout gates remain unresolved prerequisites, tracked in #446.

See [mcp-registry-probe.json](../compatibility/mcp-registry-probe.json) and the
reproducible tests/integration/mcp_registry_probe.py. The probe demonstrates
thread configuration/registry behavior; it does not attest isolation or sending.

The [operator runbook](OPERATOR-RUNBOOK.md) generates commands from the existing
host inventory and chosen endpoints. The host pair was not supplied here. Its
source paths, actual UID, router ID, service labels and runtime paths are learned
before generating HOST-RUNBOOK.md, rather than hardcoded from this Linux sandbox.

Pending host gates: macOS ownership/projection checks; actual read-only mounts
and inaccessible journals/engine/other namespaces; existing fleet health; router
admission and first sight; authenticated START1 inference; both real round trips
with attachment tool traces and accepted results; idle/restart/recreation/fault
checks. Pilot verification reports these separately and never claims rollout
readiness from incomplete evidence. Do not apply fleet changes before engineering
checks, source publication/review and operator approval of the concrete plan.

