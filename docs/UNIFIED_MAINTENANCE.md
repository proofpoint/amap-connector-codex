# Unified history adoption and queue disposition

Status: experimental implementation, pending independent review and adapter
acceptance. These are portable connector commands, not installation or fleet
lifecycle commands. The operator stops and starts the instance using its
adapter's reviewed runbook. No command below is an instruction to install an
unreviewed branch or change a running fleet.

## Adopt one saved conversation

Adoption selects one conversation; it does not merge the old interactive and
supervisor histories. The other saved conversation and the entire delivery
journal remain intact. Choose an explicit thread UUID in the same workspace.
Do not use `--last` or delete the journal to choose it.

1. Preserve the previous connector configuration and its referenced protected
   files, along with the retained journal and native Codex home. Their contents
   must remain available until adoption completes; the previous configuration
   must still match the journal fingerprint. An adapter must plan this before
   overwriting generated files.
2. Inspect existing delivery state with the previous configuration. Resolve any
   in-flight AMAP/operator work and ambiguous publication through its existing
   audited workflow. Adoption refuses unresolved work and native queued input;
   it never cancels them implicitly.
3. Stop the old service and all native clients. Install reviewed source and
   prepare the target configuration with explicit unified mode, keeping the
   instance identity, workspace, self address, lanes, outcomes and state paths.
   Keep the target service stopped while adopting.
4. Run static configuration validation and the explicit transition:

```sh
amap-codex --config /etc/amap-connector/unified.toml doctor
amap-codex --config /etc/amap-connector/unified.toml unified-adopt \
  --previous-config /etc/amap-connector/previous.toml \
  --thread 11111111-1111-4111-8111-111111111111 \
  --note 'Operator selected this saved conversation for unified delivery'
```

The example UUID is a placeholder; use the operator-selected saved UUID.
The command acquires the ordinary private controller lock and all lane claims.
It starts a private owned backend, reads the complete saved history and queue
without loading the thread, and refuses active or queued work and another
workspace. It records a durable adoption intent before refreshing the selected
thread's protected policy and tools. No model input is submitted.

The command verifies the effective pane settings, protected MCP registry, idle
state and exact ordered history before committing the new fingerprint, canonical
binding and policy hash atomically. Its receipt includes the retained-history
SHA256 and previous/selected IDs. The audit keeps both transition metadata and
the operator's note. Start the service through the adapter only after success;
its next start uses the same ID and policy. Terminal attachment uses the ordinary
unified wrapper, as described in [the runtime contract](UNIFIED_RUNTIME.md).

If the command is interrupted after intent commit, ordinary startup and ordinary
configuration migration refuse the planned maintenance. Re-run the same explicit
adoption command to re-observe the original history and policy. Changed history,
selection or policy requires investigation; no reset or automatic replacement
conversation is provided. A native policy refresh can precede journal commit,
which is why the stopped-service requirement and durable hold matter.

## Dispose of unresolved incoming work

Stop the service and all native clients first. Inspect `status` and choose an
explicit journal event ID. The unified recovery command supports `hold` and
`handled`, with a required audit note. It does not support `retry`: missing
history or a missing queued entry does not establish that prior work was unsent.

```sh
amap-codex --config /etc/amap-connector/unified.toml status
amap-codex --config /etc/amap-connector/unified.toml recover EVENT_ID \
  --action handled --cancel-queued \
  --note 'Operator inspected the retained work and cancelled its pending input'
```

`--cancel-queued` authorizes removal of that event's recorded pending native
input. Without it, a pending entry blocks disposition. The command never calls
`thread/resume`, `turn/start` or `thread/queue/add`; it inspects and deletes before
Codex can drain a saved queue. It matches the canonical thread, durable client
identity and known queue association. Duplicated identities or changed queue
associations require investigation and are refused.

A durable deletion intent records the exact queue/client IDs and hashes of the
observed input and original history before writing. Positive acknowledgement,
absence of that pending identity and unchanged original history are verified
before final disposition. A lost acknowledgement leaves a planned hold that
blocks ordinary startup and migration. Re-run the explicit recovery command
with `--cancel-queued`; it must re-observe matching pending input, or its absence
with unchanged original history, before confirming the deletion. It never
re-adds the model input.

`handled` records `operator_handled`, not a native execution success or a
fabricated delivered outcome. `hold` leaves the event uncertain with a sticky
blocked intent. It stops further delivery until the operator resolves it; a
subsequent explicit `handled` can finish that disposition. Keep
`--cancel-queued` on subsequent commands for an existing deletion record.
Publication results and outbound send acknowledgements retain their separate
runtime-owned recovery rules.

## Verification and remaining gates

```sh
python -m pytest tests/test_unified_maintenance.py
python tests/mutations/unified_maintenance.py
PYTHONPATH=src python tests/integration/unified_maintenance_probe.py \
  --output /tmp/unified-maintenance.json
```

The native probe uses disposable credential-free homes and synthetic localhost
inference. It selects a different saved history from the old journal binding,
verifies exact retained history and the untouched other conversation, and sees
the next peer response in the adopted pane. A second scenario refuses changed
queued artifacts after SIGKILL, explicitly cancels before resume and verifies no
queued/human replay or fabricated delivery. The committed
[build-specific receipt](../compatibility/unified-maintenance-0.164.0.json) is
source evidence; it is not an actual-router round trip or host deployment test.
Twenty critical maintenance mutations run only on independent scratch copies.
Independent source review, adapter configuration/verification and the operator's
host pilot remain gates. Additional native controls and exceptional history
repair need their own contracts; these commands do not perform arbitrary
conversation repair or merge histories.
