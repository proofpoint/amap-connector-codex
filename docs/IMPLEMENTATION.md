# Implementation record

Each discrete step used plan → implement → verify. Mechanical import and
deployment documentation used `gpt-6-luna`; admission, durable state, failure
review and tests used `gpt-6.1-sol`. The primary agent integrated the protocol,
supervisor and CLI. Agents received bounded file ownership and concise context;
there was no recursive delegation. Live inference used low reasoning effort
and synthetic data. No precise monetary cost is claimed.

| Step | Plan | Implementation | Verification |
| --- | --- | --- | --- |
| Source baseline | Preserve pinned contracts, signatures and licenses | Vendored MCP read/submit tools, canonical claims, schemas and provenance; targeted version/metadata patches | Imported regressions and fixture validator pass |
| Protocol gate | Inspect installed schema before relying on experimental fields | Bounded JSONL framing, continuous reader, handshake, correlated RPCs, tool-output delivery and instruction overrides | Fault fixtures plus authenticated host MCP read, completion and persisted-input resume |
| Admission and state | Separate admitted metadata from sender content and acceptance from execution | Lane validation, recipient/body checks, distinct IDs, SQLite FULL transactions, attempts and atomic outcomes | Version/shape/address/attachment tests, publication grace, conflicts, retries, outcome crash cases |
| Controller | Commit dispatch before writes; serialize on a persisted target | Canonical spool claims, state lock, thread binding, orphan detection, bounded queue, reconciliation and shutdown | Busy/backlog, early completion, death/restart, accepted dedupe, missing history, contention and cleanup tests |
| Operations | Provide explicit inspection and audited recovery | `run`, read-only `doctor`/`status`, bounded `doctor --probe`, handled/hold and evidence-required retry | CLI tests confirm no status mutation, claim exclusion, version pin and audit records |
| Deployment and documentation | Make host/agent paths and ownership explicit | Compose/foreground launcher, read-only inputs and nested result views, operator instructions, CI and operations guide | TOML parsing, Codex MCP config loading, launcher syntax, package installation and deterministic suite |

The local implementation is complete. Final validation: 154 deterministic tests,
eight subtests, and 44 pinned AMAP fixtures passed on Python 3.13.5. Editable
installation, wheel build, installed CLI help, TOML parsing and launcher syntax
also passed. CI is configured for Python 3.11 and 3.13; 3.11 was unavailable
locally. The actual isolated router demonstration
is pending because the deployment is absent; [compatibility/README.md](../compatibility/README.md)
lists the required evidence. No release was published and no Git repository
was initialized by this task.
