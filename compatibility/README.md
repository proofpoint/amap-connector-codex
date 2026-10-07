# Codex compatibility evidence

Reference target: **codex-cli 0.160.1**, Linux aarch64, inspected October 6,
2026. This is a pilot implementation; the isolated deployment release gate
remains open.

The installed binary generated the committed protocol subset with
`codex app-server generate-json-schema --out DIRECTORY`.
[schema-manifest.json](schema-manifest.json) records hashes. The client enables
the experimental API, completes `initialize` before `initialized`, and uses
the generated `developerInstructions` field on both start and resume.
The launcher's actual initialization user-agent version is checked before
starting a thread. A deployment states which reviewed build it runs (`SUPPORTED_CODEX_VERSIONS` in
`src/amap_codex/config.py`); any other is refused. A move between reviewed builds
keeps the journal and its thread and is recorded in its audit table. Add a build
only after re-running these probes against it.

[live-host-probe.json](live-host-probe.json) records a real authenticated
host-side run using the account's selected `gpt-6.1-sol`, low reasoning effort,
one synthetic mail body, and one explicitly permitted local MCP read tool:

- Standalone tool output was accepted with `input: []` and the fixed
  `amap_connector.amap_notice` source.
- The model called `inbox.read_message`, fetched the synthetic body, returned
  its marker, and completed the turn.
- `thread/read` exposed the original connector input as `functionCallOutput`.
- A fresh app-server resumed the same persistent thread and exposed that input.

The first host probe used the implicit MCP approval policy with headless
`approvalPolicy: never`. Delivery was accepted, but the MCP read was denied;
turn completion alone did not prove the message had been fetched. The corrected
probe and deployment example explicitly permit the exact AMAP tool allowlists
using `default_tools_approval_mode: approve`. This permission covers local reads
and inert submissions; the runtime retains outbound sending authority.

Reproduce the host probe from a source checkout with authenticated Codex:

```sh
PYTHONPATH=src python tests/integration/live_probe.py \
  --model MODEL_AVAILABLE_TO_YOUR_ACCOUNT \
  --output compatibility/live-host-probe.json
```

The fake-server suite verifies request correlation, early completion,
timeout/partial-boundary uncertainty, subprocess death, generic RPC errors,
approval cancellation, user-input interruption, conservative history matching,
serial turns, claim contention, restart, and cleanup. Response envelopes are
checked against generated schemas. A 25 MiB binary attachment test measures
the imported reader's path/metadata output; bytes are not serialized over this
control interface. Frames default to 32 MiB, above the reader's 8 MiB published
JSON bound and 64 KiB inline attachment content.

## Required deployment verification

No Docker executable, real AMAP router, namespace, or existing sandbox launcher
was available in this workspace. These remain **pending**, with no claim of
production readiness:

| Gate | Remaining evidence |
| --- | --- |
| Isolated launcher | Foreground stdio purity, exact image/version, stable container orphan detection and remote child cleanup |
| OS isolation | Read-only write/rename/chmod/symlink attempts, another mailbox/journal/credential/socket access failures |
| MCP deployment | All three required instances initialize; sidecars, nested result/processed mounts and atomic outbound staging work |
| Trusted instructions | Workflow remains effective during resumed inference, with operator configuration outside writable work |
| Live headless requests | Actual approval and user-input protocol requests resolve visibly without permission expansion |
| Router round trip | Real admitted peer request, distinct notice/message IDs, attachment, gated reply, runtime send result, and no reply loop |
| Outcomes | Router agrees to `outbound/ext/codex/outcomes`, vocabulary and crash publication semantics |
| Recovery demo | Restart/duplicate scan and deliberately interrupted dispatch under actual isolation |
| Runtime contract | Canonical claim paths/PID namespace, publish ordering, retention window, authenticated model egress |

The wake-up fallback is implemented and fixture-tested but has no live proof;
keep `delivery_mode = "tool_output"` for this build. A failed accepted turn is
an execution diagnostic and is never automatically replayed. Missing original
input in partial history never establishes non-delivery.

Protocol and configuration sources: [official app-server interface](https://learn.chatgpt.com/docs/app-server)
and [official MCP configuration](https://learn.chatgpt.com/docs/extend/mcp).

The [three-server startup probe](mcp-registry-probe.json) uses the pinned actual
app-server and all three vendored MCP binaries. It creates a thread and validates
the effective server names and exact tool sets without any inference or submission.
Reproduce with `python tests/integration/mcp_registry_probe.py --model MODEL
--output compatibility/mcp-registry-probe.json`. Thread overrides are sent on
start/resume and the actual registry is checked: CLI TOML map overrides merge
ambient MCP registrations rather than necessarily replacing them. This probe
is host protocol evidence and does not close the live deployment gates.
