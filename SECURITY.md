# Security policy

## Reporting a vulnerability

Report a suspected vulnerability privately through GitHub's
[private vulnerability reporting](https://github.com/proofpoint/amap-connector-codex/security/advisories/new)
for this repository. Please do not open a public issue for it.

Include the affected commit, the configuration involved, and the steps that
reproduce the problem. Leave out real credentials and real message content.

## Scope

In scope: the supervisor and its journal, notice admission, the MCP read and
submit tools, the operator CLI, and the trusted-configuration checks that keep
a thread's tools to the three AMAP servers.

The connector relies on its deployment for isolation and on the AMAP router
for authorization and sending ([docs/DESIGN.md](docs/DESIGN.md)). It does not
scan attachments, and its journal is reachable by the agent it serves (§1.1).
Report a weakness in those boundaries to the component that owns it.
