# Shared authentication and interrupted-turn checks

These opt-in probes exercise real Codex behavior in an operator-selected test
environment. They are separate from CI fixtures. Use a dedicated test instance,
retain its state and record the actual build. The probes contain no particular
isolation adapter or host restart command.

## Shared managed authentication

Keep a real interactive Codex pane and an in-environment supervisor running
with the same existing Codex home. Complete a synthetic, no-tool marker turn
in each before the refresh. From that environment, using the prepared
connector test environment:

```sh
python tests/integration/shared_auth_probe.py \
  --codex-home "$TEST_CODEX_HOME" \
  --refresh --output "$TEST_EVIDENCE_DIR/shared-auth.json"
```

`TEST_CODEX_HOME` is an existing test login, not a new credential copy.
`--refresh` performs real managed token rotation. It starts two app-servers
with separate auth managers, requests refresh concurrently, checks refresh
metadata changed and confirms both still report the same account. The evidence
omits credentials, account identity and home paths. Without `--refresh`, it
only demonstrates shared read access.

Afterward, complete another no-tool marker turn in each original pane and
supervisor. Inspect their actual agent responses and terminal turn statuses,
not markers echoed from the input. Record the result separately: the refresh
probe alone does not establish pane or supervisor inference behavior. Keep
raw logs and authentication local; publish only sanitized observations.

The official [app-server account interface](https://learn.chatgpt.com/docs/app-server)
documents managed `account/read` with `refreshToken: true`; the installed
0.161.0 binary's generated schema also describes proactive refresh. This is
not the externally managed token mode.

## Restart the isolation environment during a turn

Run the recovery probe in a dedicated test environment using its existing
authentication. `TEST_PROBE_ROOT` must be a new directory on storage that
survives the restart and has the same path afterward. The probe creates its
own synthetic namespace, journal, queue and workspace command. It does not
use real fleet spools, peers or a router.

Before starting, obtain the current runtime incarnation from the isolation
system's read-only state API and retain the raw result privately. Set
`TEST_RUNTIME_BEFORE` to that measured identity. Choose `TEST_SANDBOX_POLICY`
according to the existing environment's policy:

```sh
python tests/integration/restart_probe.py prepare \
  --root "$TEST_PROBE_ROOT" --sandbox "$TEST_SANDBOX_POLICY"
```

The default inner policy is `workspace-write`. Some outer isolation systems
cannot create a nested Codex sandbox namespace. When the outer isolation is
the approved execution boundary, an explicit `danger-full-access` probe
profile can rely on that boundary. Do not change a deployed policy just to
get a green result. The probe's command only writes its own markers and waits
for at most 180 seconds; it performs one real model turn.

Wait for JSON with `phase: active`, `turn_accepted: true` and
`workspace_command_entered: true`. The process remains attached. **Then the
operator-host runner stops and restarts that dedicated isolation environment
through its normal lifecycle interface.** This operation is external to the
probe. Confirm the old runtime and all its processes are gone and obtain the
replacement's actual incarnation as `TEST_RUNTIME_AFTER`.

Run verification in the replacement environment, against the retained root:

```sh
python tests/integration/restart_probe.py verify \
  --root "$TEST_PROBE_ROOT" \
  --runtime-before "$TEST_RUNTIME_BEFORE" \
  --runtime-after "$TEST_RUNTIME_AFTER" \
  --output "$TEST_EVIDENCE_DIR/sandbox-recovery.json"
```

The distinct runtime IDs are **operator-provided restart evidence**, not an
identity measurement performed by this script. Retain their producer output
with the test report. The verifier checks unchanged configuration/thread,
the exact original operator input and turn, one workspace-command execution,
and no new dispatch over two idle cycles. The work must be terminal or visibly
uncertain; an unexplained still-active turn fails. Do not edit the checkpoint,
reset the journal, rerun the queued instruction or migrate to force a pass.

This synthetic supervisor check establishes no real-router sending or
attachment behavior. A deployment acceptance test must separately confirm
restart of its normal supervisor and retained production mount/claim mapping.

## Smaller process-only experiment

Where host restart control is unavailable, a new probe root can exercise a
real app-server/controller crash within the existing isolation environment:

```sh
python tests/integration/restart_probe.py prepare \
  --root "$TEST_PROCESS_PROBE_ROOT" --sandbox "$TEST_SANDBOX_POLICY" \
  --fault-app-server
```

Exit status **77** is the intentional crash after the active checkpoint.
It kills the probe-owned app-server group and leaves crash records and claims
for recovery. The bounded workspace helper is released to avoid leaving it
waiting. Then:

```sh
python tests/integration/restart_probe.py verify \
  --root "$TEST_PROCESS_PROBE_ROOT" \
  --output "$TEST_EVIDENCE_DIR/process-recovery.json"
```

This result is explicitly labelled **no sandbox restart or router
attestation**. It cannot replace the external lifecycle test. Preserve failed
roots for diagnosis; successful roots can be removed through the test
environment's normal cleanup after verifying its probe processes have exited.
