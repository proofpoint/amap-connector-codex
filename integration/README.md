# Review and integration order

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


Review [Sandy #444](https://github.com/rappdw/sandy/pull/444),
[router #1](https://github.com/proofpoint/amap-router-local/pull/1) and
[deployment #30](https://github.com/proofpoint/amap-deploy-sandy/pull/30).
All are drafts with auto-merge disabled. review-index.json records their
baselines and prepared heads; repos/ contains independent local working copies
and is excluded from the connector source repository.

The canonical connector repo will be proofpoint/amap-connector-codex when
created. Publish/review its initial sources and pin that reviewed commit before
fleet rollout. Until then, compatibility/engineering-source-lock.json records
the local artifact, and dist/ holds the wheel. No canonical Git pin is invented.

Review the connector launcher-control contract before the adapter. Review and
manually merge the generic Sandy and router capabilities before the deployment
adapter; the latter PR links both dependencies. Require final-head CI and the
host macOS checks. No merge or fleet application was performed here.

Then follow docs/OPERATOR-RUNBOOK.md on the existing host. Its generator reads
actual Sandy identities and runtime mounts, writes a reviewable plan and renders
HOST-RUNBOOK.md before apply. Source/preimage/container drift refuses application.
Retain existing router state and verify all unchanged Claude endpoints as well
as both pilot endpoints. Live inference/round-trip/recovery gates remain pending.
