# Generic isolation capability handoff

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


Implementation status: protected submounts are under review; remote execution is deferred to #446.
See [verification and PRs](../ENGINEERING-VERIFICATION.md) and the
[operator runbook](../OPERATOR-RUNBOOK.md). The requirements below remain the
review/acceptance contract; host-only gates are pending, and nothing is auto merged.


Provide the general mount and execution guarantees needed by the Codex
deployment in [the rollout](../ROLLOUT.md). Keep AMAP names, Codex protocol,
mailbox policy, model choice and journals out of Sandy.

## Protected nested feature mounts

Add a manifest capability to mount a read-only child tree below a declared
feature mount, including below a writable parent. The deployment needs a
writable request directory containing protected runtime-owned result and
processed views; this is a general isolation requirement.

Keep all destinations computed by Sandy beneath the feature's own mount root.
Do not add arbitrary absolute target paths. Validate a relative child path and
the parent mount reference, reject traversal and symlink escapes, and enforce
parent-before-child mount ordering. Ensure writable aliases cannot reach the
protected child trees or another namespace. Expose the resolved submounts and
read/write modes through normal introspection.

Allow the deployment to select a mount projection declaratively when different
feature selections need different children. A read-only empty child view must
be able to hide a host-private sibling beneath a writable parent. If this needs
a mount selection/profile extension, keep its vocabulary generic and preserve
existing defaults; do not hardcode agent or connector names. Validate overlapping
profiles and resolved aliases before launch.

Publish support in `--print-schema` and validate old/new manifests deliberately.
An unsupported required field must fail launch rather than produce a writable
runtime view. Preserve current manifests and feature selection semantics.
Use neutral fixtures such as writable `work` containing protected `results`.

## Future lifecycle design — deferred to Sandy #446

This portion requires a maintainer design decision in [#446](https://github.com/rappdw/sandy/issues/446).
It is not part of the protected-submount PR. Do not add a side-file helper or a
root execution path to that PR. The following requirements describe a future
approved implementation, owned by a consumer payload or a generalized Sandy
interface as decided there. The actual Sandy root filesystem is read-only; only
`/tmp` and `/home/sandy` are tmpfs. `/run` is not writable.

The existing piped `--exec` supplies the correct uid, gid, `HOME` and non-TTY
stdio. Confirm that behavior in tests. Establish a generic way for a host
adapter to identify, inspect and stop one owned execution plus its children
inside a running sandbox, including after the host attach process dies.

First determine whether the existing runtime interfaces suffice for the
deployment adapter. If so, document and test that route without a new Sandy
command. If a capability is missing, describe a generic interface in that issue for
review before implementation; use opaque execution IDs and sandbox identity. Do not add
an app-server-specific command or expose the container engine inside the agent.

Stopping a local Docker client is not proof of remote termination. Stop must
target the recorded execution, verify its identity against PID reuse and reap
its process group/children. An unavailable runtime or lost stop result is
UNKNOWN and cannot authorize a second execution. Inspection must also detect
a surviving owned execution after host SIGKILL. Preserve raw command stdout
for JSONL; diagnostics/control metadata use separate channels.

Container recreation/removal must positively terminate owned executions and
report that transition to host consumers. Resume of a model thread and decisions
about uncertain work remain connector responsibilities. Preserve Sandy's
credential provisioning and existing filesystem/network boundaries.

## Acceptance criteria

- Existing feature manifests and ordinary `--exec` remain compatible.
- Agent-uid writes, renames, chmod and symlink substitution cannot mutate a
  protected nested tree or reach a writable alias of it.
- A selected empty child projection hides the real host tree; other selections
  retain their declared view without leaking an alias or changing old defaults.
- Invalid parents/child paths and unsupported required capabilities fail visibly.
- Piped execution preserves stdin/stdout bytes, uid, home and command exit status.
- Graceful stop, host-client SIGKILL, child processes and container recreation
  have tested exact-execution cleanup/orphan behavior on the actual macOS host
  Docker setup and supported Linux setup.
- Introspection distinguishes running, stopped and unknown state, and generic
  tests contain no AMAP or Codex logic.

Deliver a capability description, schema/introspection updates when needed,
compatibility notes and runtime tests. The Sandy AMAP deployment owns any helper
that translates these capabilities into the connector's launcher-control JSON.

The inspected
[feature contract](https://github.com/rappdw/sandy/blob/451ed040ee60fffae8e79191847fc4829ec756e5/docs/design/FEATURE-MANIFEST.md)
computes feature destinations from mount names. Recheck the actual installed
capabilities before defining the extension.
