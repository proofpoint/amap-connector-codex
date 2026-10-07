# Operator runbook: one Codex sandbox

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


Engineering changes are delivered as separate PRs. Review and merge them manually;
none are auto merged. This workspace has not changed the host fleet. The host
workspace paths and chosen Claude peer have not been supplied, so the generator
uses actual Sandy inventory to render exact commands instead of guessed slugs.

The canonical connector repository will be `proofpoint/amap-connector-codex`
when created. Its first source publication/PR is pending repository creation.
Until then, retain the reviewed local source hashes; do not substitute an
unreviewed checkout or invent a Git pin.

Use the reviewed connector source, Sandy, router and deployment revisions.
Install Python 3.11+ on the host. Keep the Codex connector and deployment sources
at stable absolute paths: host services import these files. Install the connector
into a dedicated host venv and select a model available to your account.

Set the following operator inputs to their actual absolute paths and identities:

```bash
export CODEX_SOURCE=/absolute/path/amap-connector-codex
export SANDY_SOURCE=/absolute/path/sandy
export ROUTER_SOURCE=/absolute/path/amap-router-local
export DEPLOY_SOURCE=/absolute/path/amap-deploy-sandy
export CODEX_WORKSPACE=/absolute/path/new-dedicated-codex-workspace
export CLAUDE_WORKSPACE=/absolute/path/existing-claude-workspace
export ROUTER_CONTAINER=existing-router-container-name
export CODEX_MODEL=model-available-to-your-account
export PILOT_DIR="$HOME/amap-codex-pilot"
export PILOT_PYTHON="$PILOT_DIR/venv/bin/python"
```

These variables are inputs, not commands to execute with the example values.
The existing fleet must verify healthy before preparation. Run normal deployment
verification before any changes and retain its output:

```bash
mkdir -p "$PILOT_DIR"
chmod 700 "$PILOT_DIR"
python3 "$DEPLOY_SOURCE/amap-sandy.py" verify > "$PILOT_DIR/baseline.txt"
python3 -c 'import sys; assert sys.version_info >= (3, 11), "select a Python 3.11+ interpreter"'
python3 -m venv "$PILOT_DIR/venv"
"$PILOT_PYTHON" -m pip install -e "$CODEX_SOURCE[test]"
"$PILOT_PYTHON" -m pytest "$CODEX_SOURCE/tests"
"$PILOT_PYTHON" "$CODEX_SOURCE/vendor/amap-spec/fixtures/validate.py"
(cd "$SANDY_SOURCE" && bash test/run-tests.sh)
```

Run router and deployment regression suites from their own checkouts, following
their existing sibling setup. Require the Sandy Linux Docker execution checks through its existing full CI,
plus the same host projection/ownership checks on the existing macOS host, to pass before the fleet apply.
Docker skips are not successful runtime verification.

Provision the dedicated Codex workspace through the existing normal Sandy
procedure. It must be a fresh namespace, outside the current AMAP selection,
report only Codex, and already have authenticated Codex 0.160.1. Keep it idle.
Choose a selected, single-Claude peer with exactly one delivery target. The
prepare command requires both normally provisioned containers to be running.

For a new permitted workspace, use Sandy's real provision/start path:

```bash
mkdir -p "$CODEX_WORKSPACE"
SANDY_AGENT=codex "$SANDY_SOURCE/sandy" --provision --workspace "$CODEX_WORKSPACE"
"$SANDY_SOURCE/sandy" --start --workspace "$CODEX_WORKSPACE" --agent codex
"$SANDY_SOURCE/sandy" --exec --workspace "$CODEX_WORKSPACE" -- codex --version
```

Require the reported version to be exactly `codex-cli 0.160.1` and finish normal
Sandy auth provisioning before preparation. These operator provisioning actions
come after engineering review/checks; none were executed in this workspace.

```bash
"$PILOT_PYTHON" "$DEPLOY_SOURCE/codex_pilot.py" \
  --sandy "$SANDY_SOURCE/sandy" inventory > "$PILOT_DIR/inventory.json"
"$PILOT_PYTHON" "$DEPLOY_SOURCE/codex_pilot.py" \
  --sandy "$SANDY_SOURCE/sandy" prepare \
  --codex-workspace "$CODEX_WORKSPACE" --claude-workspace "$CLAUDE_WORKSPACE" \
  --codex-src "$CODEX_SOURCE" --sandy-src "$SANDY_SOURCE" \
  --router-src "$ROUTER_SOURCE" --router-container "$ROUTER_CONTAINER" \
  --model "$CODEX_MODEL" --output "$PILOT_DIR/plan.json"
"$PILOT_PYTHON" "$DEPLOY_SOURCE/codex_pilot.py" \
  --plan "$PILOT_DIR/plan.json" runbook --output "$PILOT_DIR/HOST-RUNBOOK.md"
```

Inventory and preparation make no fleet changes or inference requests. Review
plan.json and HOST-RUNBOOK.md. They contain the real host UID, interpreter,
workspace/container identities, service names, absolute paths, preimage/source
hashes, retained router state and the exact C↔B policy delta. Old-to-old policy
is retained; future membership is frozen and requires a new plan. Changes after
preview cause apply to refuse.

The generated runbook contains commands for apply, scoped sandbox restarts,
router image rebuild and replacement by its recorded full ID, first-sight check,
agent-UID mutation probes, doctor, both host services, round trips, cleanup and
scoped rollback. Run one stage at a time and retain outputs. The doctor probe
starts/resumes the same thread without delivering a notice or invoking inference.
Stop on FAIL or UNKNOWN. Require a healthy check of all other selected Claude
sandboxes through their existing session/relay verification; the pilot checker
only checks its two managed endpoints and does not replace that fleet gate.

The generated START1 kickoff performs a low-effort synthetic inference using
only inbox/delegation list tools on the same controller. Retain its actual tool
traces and terminal marker before any peer traffic; it never authorizes a send.

The initiating turn must end after checking the accepted submission. The serial
controller delivers the reply in a later turn on the same persistent thread;
waiting for that notice within the initiating turn would block delivery.

R1 uses the existing Codex controller's private kickoff queue. R2 prints an
instruction to paste into the existing Claude session. Both commands prepare a
nonce-bearing fixture and do not reuse an already prepared run ID. The evidence
checker verifies two accepted runtime submissions, peer message correlation and
marker/hash. Retain actual attachment-read tool traces, receiving-turn association
and two later idle polls; those gates remain UNKNOWN until observed. Acceptance
and a terminal turn alone do not complete the demonstration.

After the round trips, stop and restart only the Codex host service using the
platform service commands in the generated runbook. Compare thread ID, journal
states and submission counts. Separately exercise host-controller SIGKILL while
the bound execution survives; a replacement must refuse, cleanup must target that
recorded execution, and history must reconcile original inputs before dispatch
resumes. Do the same for normal Sandy container recreation. Preserve credentials,
thread history, journal, spools and all router state.

For an uncertain operator kickoff, stop the service and use one audited disposition:

```bash
amap-codex --config /actual/rendered/controller.toml recover-operator R1 \
  --action hold --note 'Original-input evidence remains incomplete'
amap-codex --config /actual/rendered/controller.toml recover-operator R1 \
  --action handled --note 'Operator inspected effects and retired this run'
```

Handled retires a run without claiming that it was sent or automatically resending
it. There is no operator-kickoff retry action. Runtime cleanup must be positive
before the recovery command can obtain canonical guards. Unknown cleanup holds
ownership; do not remove records or run fleet teardown to bypass it.
