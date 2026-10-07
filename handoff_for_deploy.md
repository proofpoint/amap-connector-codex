# Handoff: finish Codex admission to the existing Sandy fleet

This handoff is for the coordinating agent in `amap-deploy-sandy`. The operator
will expose this connector workspace there through a symlink. The objective is
one dedicated Codex sandbox in the existing Claude fleet, followed by real
Codex → Claude → Codex and Claude → Codex → Claude round trips, while keeping the
connector usable with other isolation environments.

**Status at handoff, 2026-10-06: engineering is incomplete; rollout is blocked.**
The portable connector is implemented and locally tested. The router change is
ready from our engineering perspective. Sandy has two remaining protection
gaps, and the deployment implementation does not resolve its architectural
review. No fleet changes or live fleet round trips have been performed.

This assessment supersedes earlier readiness implications in
[ENGINEERING-VERIFICATION.md](docs/ENGINEERING-VERIFICATION.md), the older heads
in [review-index.json](integration/review-index.json), and the draft
[OPERATOR-RUNBOOK.md](docs/OPERATOR-RUNBOOK.md). In particular, green Sandy tests
did not cover the two protection gaps below. The old runbook's pilot commands
are not a finished or approved rollout procedure.

## Coordination and authorization

- Deliver integration changes through PRs. Keep existing PRs draft while
  prerequisites remain unresolved. **Do not auto-merge or merge them.** The
  operator makes the merge decisions.
- Complete and verify engineering before applying fleet changes. Prepare a
  concrete host-specific runbook and reviewable change plan for operator
  approval before provisioning, changing membership/configuration, restarting
  services, or running the live round trips.
- Preserve existing fleet policy, domain, spool contents, router state and
  deduplication history. An unknown lifecycle state blocks replacement; deleting
  ownership records is not a recovery procedure.
- Read each repository's contributor instructions before editing. In particular,
  [deployment CLAUDE.md](integration/repos/amap-deploy-sandy/CLAUDE.md) defines
  host-only execution, no credential acquisition, manifest-owned policy,
  provenance-based verification and sibling pinning.
- Keep Sandy mechanisms consumer-neutral. Keep Docker/Sandy/service details in
  an isolation adapter or deployment, out of the connector core. Preserve the
  default direct-local-process launcher and generic launcher-control contract.
- Use plan → implement → verify for each discrete change. Assign explicit
  repository/file ownership to agents and use separate worktrees for competing
  designs. Use a stronger model for isolation/lifecycle and architecture review;
  use a smaller model for bounded edits and documentation where appropriate.
  Reuse deterministic fixtures and avoid paid model turns until live gates.

Relative links in this document refer to the connector workspace. Resolve the
symlink to find that workspace rather than assuming the deployment checkout is
its parent. Local clones under `integration/repos/` are engineering checkouts,
not the operator's live installation.

## Review and workspace snapshot

These are the last reviewed published heads, not a claim that remote branches
cannot have advanced. Refresh PR comments, reviews, heads and CI before making
new changes or declaring readiness. All three PRs were open drafts with
auto-merge disabled.

| Repository | PR and reviewed head | Assessment |
| --- | --- | --- |
| `rappdw/sandy` | [#444](https://github.com/rappdw/sandy/pull/444), `e98b55c15358409f2ddeae531a276a5a03dc5dc5` | Blocked: cross-feature alias and validation-to-bind race |
| `proofpoint/amap-router-local` | [#1](https://github.com/proofpoint/amap-router-local/pull/1), `d4801d78367e88fd7e7ebabdc0cbd1a291e3d2da` | Ready from engineering perspective; manual review/merge still required |
| `proofpoint/amap-deploy-sandy` | [#30](https://github.com/proofpoint/amap-deploy-sandy/pull/30), `472cb20f8017bc189e324aa03ded5d7d05e2e535` | Blocked: unresolved architecture and incompatible Sandy assumptions |

The root connector workspace has no Git metadata. Its intended canonical repo
is **`proofpoint/amap-connector-codex`**, which has not yet been created/published
as part of this work. Do not invent a revision or treat a source-hash manifest
as a Git pin. The built wheel and source are reviewable local artifacts.

Local checkout cautions:

- `integration/repos/sandy` is clean at the reviewed Sandy head.
- `integration/repos/amap-router-local` is still at
  `1756fa2cfeec86eca642166a9cdd774798577dda`; its fetched
  `origin/codex-rollout-prerequisites` points to the newer reviewed head above.
  Test a separate worktree at the actual PR head, not this older local HEAD.
- `integration/repos/amap-deploy-sandy` is at the reviewed deployment head with
  **uncommitted edits** in `codex_pilot.py` and `tests/test_codex_pilot.py`.
  They add `validate_host_bind_sources()` and a regression to refuse exposure
  of private controller state and writable overlaps with host code, plan and
  venv locations. Preserve and inspect these edits before restructuring. They
  are unpublished and do not resolve the architectural objections below.
- All integration branches are named `codex-rollout-prerequisites`.

Important review context:

- Sandy [initial review](https://github.com/rappdw/sandy/pull/444#pullrequestreview-5435304328)
  requested the protected-submount split, source validation, numbered tests,
  documentation and a separate lifecycle issue. Those changes were pushed;
  [the reply](https://github.com/rappdw/sandy/pull/444#issuecomment-6027373877)
  describes that work, but does not cover our subsequently discovered blockers.
- Sandy [deployment review](https://github.com/rappdw/sandy/pull/444#pullrequestreview-5435406780)
  raises the validation-to-use concern; it remains unresolved.
- Deployment [architecture review](https://github.com/proofpoint/amap-deploy-sandy/pull/30#pullrequestreview-5435361413)
  contains the objections mapped below.
- Router [cleanup review](https://github.com/proofpoint/amap-router-local/pull/1#issuecomment-6026935832)
  and [spec clarification](https://github.com/proofpoint/amap-router-local/pull/1#issuecomment-6027029773)
  are addressed by the latest reviewed head.

Reviewers from other coding sessions posted through the author's account, so
formal review state alone is not useful evidence of approval. Our last audit
found no inline review threads; also read conversation comments and reviews.

## What the connector already provides

The reusable implementation is under [src/amap_codex](src/amap_codex). Its core
does not import Sandy, Docker or service managers. Existing defaults remain
compatible unless optional rollout settings are enabled.

- Generic bounded `inspect`/`stop` lifecycle protocol with deployment and
  execution IDs, persisted binding before spawn, strict responses and
  conservative `unknown` handling. See
  [LAUNCHER-CONTROL.md](docs/contracts/LAUNCHER-CONTROL.md) and its JSON schemas.
- Canonical host-side lane guards, durable ownership, private controller locking
  and exact execution recovery. A dead host PID does not establish that a
  remote process or its descendants have stopped. `guard-command` lets a Claude
  launcher contend on the same guards without adopting Codex model policy.
- Trusted configuration and exact MCP server/tool allowlists for thread start
  and resume, with effective registry inspection rather than trusting only
  generated configuration.
- An opt-in private operator queue with stable IDs, durable deduplication and
  serialization on the same persistent thread as incoming work. A crash after
  dispatch produces uncertainty and does not automatically replay the turn.
- CLI operations for kickoff, operator recovery, status and positive cleanup.
  Operator runs have a separate audit trail; they are not forged peer notices.

Previous verification observed the connector regression suite passing, the
vendored AMAP fixture validator passing, and a successful wheel build. The real
**Codex 0.160.1** startup probe verified all three vendored MCP servers and exact
tool sets without inference or submissions. Evidence is in
[mcp-registry-probe.json](compatibility/mcp-registry-probe.json) and the
[probe implementation](tests/integration/mcp_registry_probe.py).

[live-host-probe.json](compatibility/live-host-probe.json) records an earlier
host-side inference/resume probe. It is **not** proof of Sandy isolation, router
admission or fleet round trips. [engineering-source-lock.json](compatibility/engineering-source-lock.json)
records source hashes, not a publishable sibling commit. CI definitions exist
locally but have not run as a published connector repository.

The concrete Sandy lifecycle adapter remains unresolved. The generic contract
document still describes timing for the old Sandy root helper; revise those
adapter-specific statements when the accepted replacement is implemented.

## Sandy: protect the complete effective mount set

#444 now contains only protected submounts and their documentation/tests.
`managed_exec.py`, its pytest tests, the separate submount pytest file and the
`run-isolation-contracts.sh` hook were removed. Keep lifecycle work separate.

Implemented submount behavior includes profile selection, read-only children,
relative paths, rejection of symlink components in child endpoints, matching
existing source/destination types, and duplicate/overlapping computed
destinations. **Destinations must already exist**; there is no Docker-created
stub contract. Introspection adds `manifest.submount_keys` while retaining
`schema_version: 4`, documented in SPEC_INTROSPECTION, SPECIFICATION and CLAUDE's
Features section.

The numbered §200 tests in `test/run-tests.sh` exercise refusal cases, actual
launch argument construction behind runtime stubs, jq/Node parity and guard
mutations on scratch Sandy copies. They add no pip installation or image pull.
[CI passed](https://github.com/rappdw/sandy/actions/runs/37543756977), but the
following cases were missing and remain blockers.

### Gap 1: a different selected feature can expose the protected source RW

The source-alias loop in `_sandy_fm_apply` compares the child source only with
writable sources in the current feature. It must cover **every effective
selected writable source in the container**, including canonical aliases,
independently of feature enumeration order.

Reproduction at the reviewed head (already reproduced without Docker):

```text
features/a/work/results/           existing child destination
features/a/protected/              protected source
features/b/alias -> ../a/protected  writable alias from another feature
```

Feature A `feature.json`:

```json
{
  "sandboxes": {"include": ["*"]},
  "agents": {"include": ["*"]},
  "mounts": [{"name": "work", "from": "work", "mode": "rw"}],
  "submounts": [{"parent": "work", "path": "results", "from": "protected"}]
}
```

Feature B `feature.json`:

```json
{
  "sandboxes": {"include": ["*"]},
  "agents": {"include": ["*"]},
  "mounts": [{"name": "alias", "from": "alias", "mode": "rw"}]
}
```

`_sandy_fm_apply FEATURES_ROOT example /workspace codex 1` returns success and
emits A's protected source RO and B's alias RW. Both bind the same host data.
Regular writable mount sources permit symlinks in the legacy path, so the
submount endpoint checks do not stop this case.

Fix the complete-set validation, retain the destination check, and test equal,
ancestor and descendant canonical source overlaps, both feature orderings,
parent sources, agent profiles, and selected versus excluded features. Keep the
review's original refused regression too:

```json
{
  "mounts": [{"name": "work", "from": "work", "mode": "rw"}],
  "submounts": [{"parent": "work", "path": "results", "from": "work/data"}]
}
```

### Gap 2: mutable writable bind paths can change after validation

A safe initial layout can become unsafe before Docker resolves the emitted
mount paths. Reproduced as a filesystem/path-resolution simulation:

```json
{
  "mounts": [
    {"name": "work", "from": "work", "mode": "rw"},
    {"name": "data", "from": "work/data", "mode": "rw"}
  ],
  "submounts": [{"parent": "work", "path": "results", "from": "protected"}]
}
```

Start with existing `work/results`, empty `work/data` and separate `protected`
directories. Initial validation succeeds. An actor with write access to `work`
can replace `work/data` with a symlink to `protected`. The previously emitted
raw writable bind source then resolves to the protected source.

This was not a live Docker race test. The launch ordering makes it a credible
unclosed gap: in the reviewed `sandy`, feature preflight is around line 15157,
while stale foreground-container removal is around line 17335. The host lock
checks a host PID, and the existing-daemon gate covers daemon-labelled
containers. An orphaned foreground container is not ruled out by those checks.

Choose and prove a robust lifetime/order or pinned-source solution. Stopping a
stale container before authoritative validation may be part of it; it must
also address other selected writable actors. An extra path check alone is not
proof that a concurrent writer cannot swap a bind path. Exercise the real launch
ordering and stale-container case in acceptance tests, plus deterministic
numbered regressions and scratch-copy mutations. Verify on the operator's
actual host filesystem as well as Linux CI. Do not weaken portability by
assuming a Linux-only descriptor mechanism works on macOS Docker mounts.

## Sandy lifecycle: separate design and implementation

[Sandy #446](https://github.com/rappdw/sandy/issues/446) describes the need in
consumer-neutral terms: start an on-demand process in an existing session from
the host with attached stdio, identify that exact execution and prove it and
all descendants have stopped, including after attach-client death.

A feature `entry` is session-start supervision rather than individual attached
on-demand execution. `sandy --exec` supplies the normal UID/GID/HOME/cwd for an
ordinary command but does not expose durable exact-execution inspect/stop or
descendant termination proof.

Agree on a maintained Sandy interface or a properly owned consumer adapter.
Do not simply restore the removed helper or assume root `docker exec -u 0` is
approved. Sandy has a **read-only root**, with tmpfs only at **`/tmp` and
`/home/sandy`**; the old helper's `/run/sandy-managed-exec` state fails there.

The replacement must prove exact container-incarnation binding, unique
execution IDs, durable revocation of delayed launch, cleanup of escaped
descendants, retained uncertainty after failures, minimal credential/env scope,
private control state and stderr-only diagnostics. Keep the engine/API/socket
unavailable to agents. Run the portable connector contract against this adapter,
including stop-timeout, lost response, container replacement and restart cases.

## Deployment: resolve the architecture review before rollout

The reviewed #30 head still has the objections below. Green CI does not resolve
them; Python 3.9 also skips the new pilot tests through their import gate.

| Objection | Required direction and verification |
| --- | --- |
| Parallel pilot takes over normal deployer | Replace the root `amap-codex-pilot.json` takeover/gating of install, router-config, teardown and fleet-domain. Integrate Codex through normal `install`/`verify` and retain whole-fleet verification. Use existing `Check`/`Fact` provenance, non-PASS remedies and literal-budget enforcement. `fleet=True` currently always emits UNKNOWN; new checks must be able to PASS using real producer fields and fail on their corresponding mutations. |
| Deployment acquires credentials | Remove copying sandbox `.codex/auth.json` into feature-managed host `codex-runtime/<slug>`. Authentication stays in Sandy's normal sandbox-managed scope. Separate immutable trusted configuration from authentication without bringing credentials into this repo's state. |
| Root execution and sandbox/lane writes | Remove unapproved root-helper coupling, workspace attachment writes and host-created probe directories in instances/outbox lanes. Deployment remains host-side, cannot send/receive, and does not mkdir lane trees. Assign probes and round-trip execution to an authorized harness/connector through the proper boundary; deployment owns only its feature resources. |
| Authored policy is replaced | Preserve include/exclude globs, groups and default peers rather than freezing selection into rewritten membership. The manifest is policy; Sandy reports its actual selection. Review intentional enrollment and C↔B edges explicitly, and preserve disjoint mail/delegation lanes without renderer repairs. |
| Unproven constants/defaults | Consume the connector's published compatibility contract for the reviewed Codex pin and check actual runtime. Do not independently assume a version or default missing `ROUTER_INTERVAL` to `5`; derive interval from the router's status producer, and report missing facts as UNKNOWN with a remedy. |
| Unpublished/private dependencies and Python split | Publish/pin the connector, replace private `_holder_is_live` imports with a public contract, and update full-SHA sibling pins through the existing mechanism. Decide a coherent Python support floor; do not hide pilot functionality behind skipped Python 3.9 tests. Run the actual supported functionality across its declared CI matrix. |
| Incoherent fleet migration | Replace the C+B-only host-controller migration and split `amap`/`amap-claude` feature behavior with a coherent per-agent activation path. Other Claude members must stay healthy. Each lane has exactly one consumer before and after activation; retain guards, journals and router state through migration/rollback. |

Two additional concrete incompatibilities must be eliminated:

1. `render()` still hashes/copies `sandy_src/managed_exec.py`, and launch/control
   still call `/opt/sandy/features/amap/managed_exec.py`. The file was removed
   from #444. Preparation against current Sandy cannot succeed with this
   dependency. Test with the actual reviewed Sandy checkout, not a fixture
   that synthesizes the missing helper.
2. Result/processed child sources are currently
   `instances/${slug}/outbox/{results,processed}`, inside the writable outbox
   parent source. Sandy correctly refuses this same-feature layout now. Design
   router-owned protected storage outside **all** agent-writable sources, with
   existing matching destination stubs. Update router producers, connector
   views, rendering and verification consistently; do not relax Sandy's guard
   to retain the old layout. Audit file-child runtime-config destinations too.

The deployer's `siblings.json` currently pins router
`e43dbba7ac1a44f06b4fbc5dc8c2ffb2777363c0` and Claude connector
`ca9b40647c5c002e6e6fe30b4fea54e64a76af57`, and has no Codex pin. An older router
refuses the unknown `connector_outcome_ids` key outright. The reviewed router
must be deployed before configuration starts emitting that key. Do not mistake
a siblings-main canary for verification of the deployer's installed pins.

## Router: preserve the reviewed generic behavior

The latest #1 head implements validated opaque `connector_outcome_ids`, with
legacy Claude-only defaults, safe ASCII path segments and case-fold duplicate
rejection. All configured directories share one scan budget and semantic
deduplication; this does not grant additional authority to agent-produced
outcomes. Cleanup removed obsolete single-ID constants and tests of their
implementation details. A trailing-newline validation bug was fixed.

Latest-head independent tests in a detached **Git worktree** passed, as did
[Python matrix CI](https://github.com/proofpoint/amap-router-local/actions/runs/37543411588).
The ordering implication that the first configured ID consumes budget first
could be stated more explicitly in documentation; it is not a merge blocker.
No AMAP spec identifier registry change is required for the opaque vendor IDs.

## Suggested sequence for the coordinating agent

1. **Inventory and preserve work.** Refresh reviews/heads, save the unpublished
   deployer diff, read repository instructions and map normal install/verify,
   selection, lane projections and controller ownership. Record the existing
   host baseline read-only. Do not run the old pilot apply path.
2. **Agree on contracts.** Resolve lifecycle ownership in #446, trusted config
   versus credentials, protected storage and coherent per-agent activation.
   Review these decisions across Sandy, deployment, router and connector before
   parallel implementation. Keep generic contracts separate from Sandy mapping.
3. **Close Sandy protection gaps.** Implement complete effective-mount alias
   validation and the launch lifetime/race solution on #444. Extend §200 using
   scratch mutation tests and real argument construction. Update protection
   docs and reply with evidence. Keep lifecycle out of this PR.
4. **Implement the accepted lifecycle adapter.** Deliver it in its responsible
   repository/PR and verify the generic control protocol against actual Sandy
   constraints. Do not conflate process-group cleanup with descendant proof.
5. **Publish and pin reusable dependencies.** Arrange creation/publication of
   `proofpoint/amap-connector-codex` through the operator, expose needed public
   APIs, validate the supported Python matrix and record real reviewed commits.
   Update deployment sibling pins to tested full SHAs. Router #1 can undergo
   manual review independently; do not enable its new config on older routers.
6. **Rework deployment through its existing paths.** Resolve every objection
   above, remove obsolete mechanisms/tests, preserve normal fleet verification,
   and verify installed output against the actual Sandy/router/connector
   revisions. Require producer-derived PASS fixtures and guard-specific failure
   and mutation cases. Preserve or deliberately incorporate the local overlap
   checks instead of silently discarding them.
7. **Finish proof and operator runbook.** Run all repo-required checks, lifecycle
   and protection acceptance tests, then refresh stale handoffs/evidence. Produce
   exact commands from the actual host's inventory, identities and paths, with
   expected evidence, scoped rollback and explicit approvals. Do not substitute
   this Linux engineering sandbox for the operator host.
8. **Only after manual merges and operator approval**, add one fresh dedicated
   Codex member, activate the reviewed routing edges and perform START1 plus both
   round trips. Preserve the rest of the fleet and report actual observations.

## Engineering verification commands

These commands are for disposable development checkouts, **not fleet changes**.
Select an already prepared Python 3.11+ environment with this project's test
extras installed. If dependencies need installation, do that in a development
venv; do not add pip or image pulls to Sandy's numbered test suite.

From the connector root:

```bash
python3 -m pytest tests -q
python3 vendor/amap-spec/fixtures/validate.py
```

From the reviewed Sandy checkout:

```bash
bash -n sandy
bash -n test/run-tests.sh
bash test/lint-bash32.sh
bash test/regen-config-docs.sh --check
bash test/regen-template.sh --check
bash test/regen-doctor.sh --check
bash test/run-tests.sh
```

The complete Sandy suite requires its normal runtime prerequisites. Skipped
Docker checks are not evidence of runtime isolation. Use existing CI and
maintainer-owned host acceptance for real container checks; protect the working
repo by mutating only scratch copies.

From the router's **Git checkout/worktree at the PR head**, with the reviewed
AMAP spec fixture checkout supplied:

```bash
export AMAP_SPEC_REPO=/absolute/path/to/reviewed/amap-spec
python3 -m unittest discover -s router/tests -t .
```

The path above is an input placeholder. An extracted Git archive was not an
equivalent test environment: repository-aware tests depend on Git tracking.
Use a proper worktree. From deployment, after preparing the reviewed sibling
pins in disposable development checkouts according to its own instructions:

```bash
python3 -m pytest tests -q
```

Deployment fixtures must keep Docker/router/Sandy calls stubbed as specified by
its `conftest.py`; accidentally reading the live router is not a test. Verify
the declared Python floor and confirm new functionality was exercised rather
than import-skipped. Passing tests that invent a missing helper or assume the
rejected submount layout do not count as integration proof.

## Required actual-host evidence and final runbook

The chosen Claude peer, new Codex workspace, actual UID/HOME/cwd, host source
paths, router identity, service manager and baseline inventory have not been
supplied in this engineering environment. Discover them read-only on the
existing host and derive the final commands there. Keep identifiers/credentials
out of public repository text according to deployment conventions.

The runbook must include preflight, reviewed versions/pins, preservation of
policy/state, normal provisioning/authentication, exact install/activation and
service commands, verification, both round trips, shutdown and scoped rollback.
Make operator-run steps distinct from agent engineering. No hidden API calls,
root policy changes or guessed host paths.

Require observable evidence for:

- Existing whole-fleet health before and after activation; actual Sandy
  selection, router admission and first sight; exactly one consumer per lane.
- Agent-UID write/rename/chmod/symlink attempts against protected mounts, with
  read-only mount enforcement; inaccessible controller journals, engine/socket,
  other namespaces and host code. Include cross-feature and path-swap cases.
- Authenticated Codex with the reviewed compatibility version and available
  model; the exact effective MCP registry **inside the isolation environment**.
  START1 must prove a turn without sending a message.
- C→B→C and B→C→B with unique IDs, real submission/attachment tool traces,
  router acceptance/results, correlated replies using `peer_message_id` and
  no duplicate work. Do not inject notices/outcomes to imitate an end-to-end
  result. The initiating turn finishes after accepted submission; the reply
  starts a later serialized turn on the same persistent thread. Waiting for
  the reply within the initiating turn can deadlock delivery.
- Restart, container replacement, attach-client death, controller SIGKILL,
  escaped descendants, delayed launch versus stop, lost stop response, two
  idle polling cycles and uncertain operator-run recovery. No automatic replay
  of ambiguously dispatched work, and no reclaim based only on a host PID.
- Cleanup and rollback with positive exact-execution termination evidence.
  Preserve unresolved claims, journals, spools and router deduplication state;
  report UNKNOWN rather than clearing them to manufacture success.

Additional background: [DESIGN.md](docs/DESIGN.md), [ROLLOUT.md](docs/ROLLOUT.md),
[OPERATIONS.md](docs/OPERATIONS.md), and the repository handoffs for
[connector](docs/handoffs/CODEX-CONNECTOR.md),
[Sandy](docs/handoffs/SANDY.md),
[deployment](docs/handoffs/AMAP-DEPLOY-SANDY.md) and
[router](docs/handoffs/AMAP-ROUTER-LOCAL.md). Treat their older rollout mechanics
as design history wherever they conflict with the review decisions above.
