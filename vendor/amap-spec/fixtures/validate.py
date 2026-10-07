#!/usr/bin/env python3
"""Conformance gate for amap-spec (formerly agent-mailbox-protocol).

Validates the golden fixtures against the JSON Schemas in ../schemas/:
every fixture under valid/ MUST pass; every fixture under invalid/ MUST fail.
Stdlib only (no jsonschema dep) — implements the small Schema subset the three
contract schemas use, so any conforming runtime OR connector can run this with
no counterpart present. Exit 0 iff all expectations hold.

Fixture -> schema by filename prefix: notice-* -> deliver-notice,
request-* -> submit-request, result-* -> result, identity-* -> binding-record, directory-* -> directory, roster-* -> roster,
message-* -> inbound-message (v2.1.0, spec/contract.md §5 body-spool path),
peer-* -> peer-notice (v3.1.0 DRAFT, peer-origin profile, spec/peer-origin.md:
the `<agent-id>/peer/` tree; same shape as deliver-notice with kind/mailbox
pinned to "peer", from pinned to a bare addr-spec, ids pinned to the notice_id
charset, plus message.sender_exposure).

Supported schema keywords (hand-rolled subset, all standard JSON Schema so a
full validator would agree): type, const, enum, minLength, pattern, minItems,
minimum, items, required, properties, additionalProperties, allOf, and a
single if/then pair (no else). Plus two non-schema post-checks, neither
expressible in standard JSON Schema, enforced as small named functions
alongside schema validation rather than inside the schema:
  - `_check_result_attachment_index_binding`: on a `result` doc,
    `attachments[i].index == i` (contiguous, unique, 0..n-1) — array-position
    identity for outbound attachment diagnostics.
  - `_check_content_ref_index_binding` (v3.0.0): on a `notice` or `message`
    doc, every attachment descriptor carrying `content_ref` must equal
    `f"{notice_id}.attachments/{i}"` for its own array position `i` —
    contract.md §5's "`content_ref`'s trailing `<index>` MUST equal the
    descriptor's own 0-based array index" plus the prefix binding to the
    enclosing document's own `notice_id` (neither expressible in schema).

A GREEN RUN ALSO DOES NOT PROVE YOU SUBMITTED EVERYTHING YOU PRODUCE.
This gate binds the documents a caller hands it, and nothing more. It cannot
ask "which document classes does this producer never submit?" — that question
is not answerable from inside a validator, and a producer whose only
attachment-bearing documents are built in a test file that never calls
check_document gets a green run on every one of them, forever, with the
relevant check correct and simply never reached. Reported by a runtime that
found _check_content_ref_index_binding had passed VACUOUSLY for a year while
its emitted content_ref carried a wrong prefix the check would have caught on
first contact. The gate was never wrong; it was never asked. A conformance
claim therefore has to account for coverage as well as outcome: name the
document classes you emit, and show that one of each reached the validator.

`filename` and `media_type` ARE NOT VALIDATED beyond being non-empty strings,
and that is deliberate rather than an omission. contract.md §5: "the declared
media_type is display-only — the runtime sniffs the bytes, and the sniffed
type governs policy", and both fields are attacker-controlled untrusted
display strings. Checking them here would promise a guarantee the contract
explicitly withholds, so a document carrying a garbage media_type passes this
gate by design. A green result on such a document means "that field is out of
scope for this validator", never "this shape is conformant in that respect".

NOT CHECKED HERE — runtime-enforced only. A green run of this gate does NOT
mean these hold; they are filesystem/policy invariants no JSON-document
validator can see:
  - sidecar file existence: extra file, missing file, or dir present when the
    descriptor array is empty/absent.
  - `size_bytes` / `sha256` actually matching the on-disk bytes.
  - `filename` display-sanitization and the "never a path" invariant.
  - ordinal <-> directory-entry binding on disk (the schema only checks the
    *descriptor* array's own index field, not that dir entry `N` exists).
  - inbound sidecar dirs keyed on the runtime-minted notice-id (not the
    provider message.id).
  - runtime-private staging location (outside the mounted volume).
  - write-ordering / commit-sentinel atomicity (sidecars before req-<id>.json;
    `.tmp` + os.replace).
  - caps / allowlists (type allowlist, size caps, volume quotas).
  - no `inbound/messages/notice-<id>.json` exists for a quarantined/blocked
    message (enforcement-by-absence, v2.1.0 §5) — a filesystem invariant no
    document validator can see.
  - connector tolerance of a read-only `inbound/` tree (v2.2.0 §2) — a
    behavioral invariant no document validator can see. Prescribed check is
    operational, not a fixture: run the connector against an inbound tree it
    cannot write and confirm it starts, relays every pending notice, resolves
    bodies, and submits outbound, without requiring any inbound write to
    succeed (contract.md §7).
  - ingest-by-copy (v3.0.0, contract.md §2): validation, policy evaluation,
    and message composition running over a runtime-private snapshot taken
    before any of those steps, rather than re-reading an agent-writable
    path — a filesystem/timing invariant no document validator can see.
  - write-side path discipline (v3.0.0, contract.md §2), the three
    conjunctive requirements: (a) full parent-chain resolution through
    pinned directory descriptors from the namespace root, (b) each
    directory component opened `O_NOFOLLOW|O_DIRECTORY` at the moment it is
    added to that pinned chain, and (c) the final `.tmp` component created
    `O_EXCL` and never followed, failing closed on `EEXIST`/`ELOOP` — a
    filesystem invariant no document validator can see, and one where all
    three legs must hold together (any one alone is a known-insufficient
    partial mitigation, per contract.md §2).
  - read-side open discipline (v3.0.0, contract.md §2): per-component
    no-symlink-follow opens, non-blocking opens, fstat-based regular-file
    verification on the open descriptor, and bounded reads/caps on every
    agent-influenced path — a filesystem invariant no document validator can
    see.
  - single writer, single drainer per namespace (v3.0.0, contract.md §2) — an
    operational/deployment invariant (e.g. an OS-level lock held for the
    process lifetime) no document validator can see.
  - compose-time header-boundary stripping (v3.0.0, contract.md §3): even
    where the schema's control-character patterns below are somehow
    bypassed upstream, the runtime MUST still reject or strip CR/LF/NUL
    before composing the outbound message — a runtime-side belt-and-braces
    obligation, not something this document-level gate re-checks.
  - peer-origin profile (v3.1.0 DRAFT, spec/peer-origin.md) — all operational,
    none visible to a document validator:
    - write-authority partition: no component that admits messages from a
      transport not restricted to authorised peer senders holds write
      authority over `peer/`; prescribed check is a write attempt (`touch`)
      from inside the admitting component against `peer/`, which MUST fail.
    - intake isolation: shared-transport peer candidates are held in a
      location that is neither `inbound/` nor `peer/`, unreachable from any
      agent namespace, until the verifier places them exactly once.
    - verification placement: signature verification runs in a component
      holding peer write authority, hashing body/attachment bytes itself.
    - `sender_exposure` not sender-settable: a submit-request carrying it is
      `rejected` (closed envelope); cross-host it is taken only from the
      verified statement and stripped on downgrade.
    - `in_reply_to` iff resolved: a peer notice carries `in_reply_to` only
      when the placing runtime resolved it from its own ledger; a reply key
      it cannot resolve is `rejected` (`unresolved_reply`), never stripped
      and re-routed as a fresh task.
    - at-most-once action: a connector never causes one peer message to be
      acted on more than once; an ambiguous target is held, not fanned out.

v3.0.0 note on `additionalProperties`: the three runtime-authored schemas
(deliver-notice, result, inbound-message) no longer set
`additionalProperties: false` at any level, so the `additional property ...
not allowed` check below simply never fires for them — a consumer MUST
tolerate an unrecognized member on these three artifacts (contract.md §7).
The agent-authored submit-request schema is unchanged: it stays closed at
every level, and an unrecognized member there is still a hard fixture
failure, surfaced by the runtime as a designed `rejected` result rather than
a silent drop.

v3.0.0 note on `pattern` — corrected: an earlier revision of this gate
carried a trailing-`$`-to-`\\Z` rewrite in `_pattern_compile` below and
described it as "load-bearing" for the trailing-newline case (a value like
`"status\\n"` wrongly validating against `draft.subject`'s pattern, and
likewise for recipients, `media_type`, `req_id`, `notice_id`, and
`content_ref`). That was true of THIS gate only: it fixed what this
hand-rolled validator accepts, not what the schemas themselves say, so any
implementer whose own validator does a bare Python `re.search` (the
dominant JSON Schema library's behavior), or the equivalent in Java or
.NET, still accepted the trailing newline — a green run of this gate proved
nothing about their stack. **The schemas are now fixed at the source**: every
`schemas/*.json` pattern that used to end in a bare `$` now ends in
`(?!\\n)$` instead — portable ECMA-262 syntax, not a Python-only escape —
so the trailing-newline case is rejected by any conforming regex engine
without help from this file. `_pattern_compile`'s rewrite below is now
redundant defense-in-depth for this validator specifically (harmless: it
still just tightens `$` to true-end-of-string), not the fix; do not present
it as one.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_DIR = HERE.parent / "schemas"

SCHEMA_FOR_PREFIX = {
    "directory-": "directory.schema.json",
    "roster-": "roster.schema.json",
    "notice-": "deliver-notice.schema.json",
    "request-": "submit-request.schema.json",
    "result-": "result.schema.json",
    "identity-": "binding-record.schema.json",
    "message-": "inbound-message.schema.json",
    "peer-": "peer-notice.schema.json",
}

_JSON_TYPE = {
    "object": dict, "array": list, "string": str,
    "number": (int, float), "integer": int, "boolean": bool, "null": type(None),
}


_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _pattern_compile(pattern: str) -> re.Pattern:
    """Compile a schema `pattern` with JSON-Schema (ECMA-262-equivalent) `$`
    semantics: `$` anchors to the true end of the string only. Python's own
    `$` additionally matches just before a single trailing newline, which is
    wrong here on purpose: every pattern this repo ships exists to forbid
    exactly the control characters (CR, LF, NUL, ...) that a permissive `$`
    would let slip through at the very end of a string — e.g. a `subject`
    ending in a bare `\\n` must fail, not pass. Rewriting a trailing
    unescaped `$` to `\\Z` at compile time closes that gap. Only a *trailing*
    `$` is rewritten, so a `$` appearing inside a character class — as the
    addr-spec pattern for `draft.to`/`cc` does (`[A-Za-z0-9!#$%&'*+/=?^_`
    `{|}~.-]`) — is unaffected: it is never at the end of the pattern
    string, so the trailing-anchor check never touches it. Belt-and-braces
    only as of v3.0.0's correction: every shipped pattern already closes the
    trailing-newline gap itself via `(?!\\n)` immediately before its `$`
    (module docstring, "v3.0.0 note on `pattern`"), so this rewrite is no
    longer what makes those patterns correct — it just also tightens this
    validator's own `$` to true-end-of-string."""
    cached = _PATTERN_CACHE.get(pattern)
    if cached is not None:
        return cached
    fixed = pattern
    if fixed.endswith("$") and not fixed.endswith("\\$"):
        fixed = fixed[:-1] + "\\Z"
    compiled = re.compile(fixed)
    _PATTERN_CACHE[pattern] = compiled
    return compiled


def _type_ok(value, spec) -> bool:
    types = spec if isinstance(spec, list) else [spec]
    # bool is a subclass of int in Python; keep them distinct for JSON.
    for t in types:
        py = _JSON_TYPE[t]
        if t in ("number", "integer") and isinstance(value, bool):
            continue
        if isinstance(value, py):
            return True
    return False


def validate(value, schema, path="$", errs=None):
    """Append human-readable errors to `errs`; return that list."""
    errs = errs if errs is not None else []

    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: {value!r} != const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in enum {schema['enum']}")
    if "type" in schema and not _type_ok(value, schema["type"]):
        errs.append(f"{path}: type {type(value).__name__} != {schema['type']}")
        return errs  # further checks assume the type held

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "pattern" in schema and not _pattern_compile(schema["pattern"]).search(value):
            errs.append(f"{path}: {value!r} fails pattern {schema['pattern']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value!r} below minimum {schema['minimum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{i}]", errs)

    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"{path}: missing required '{req}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    errs.append(f"{path}: additional property '{k}' not allowed")
        for k, v in value.items():
            if k in props:
                validate(v, props[k], f"{path}.{k}", errs)

    # allOf: value must satisfy every subschema.
    for sub in schema.get("allOf", []):
        validate(value, sub, path, errs)

    # if/then (this pair only, no else): apply `then` only when `if` holds.
    if "if" in schema and "then" in schema:
        scratch = []
        validate(value, schema["if"], path, scratch)
        if not scratch:
            validate(value, schema["then"], path, errs)

    return errs


def _check_result_attachment_index_binding(doc) -> list[str]:
    """Post-schema check, not expressible in standard JSON Schema: for a
    `result` doc's optional `attachments[]`, the array-position identity
    `attachments[i].index == i` must hold (contiguous, unique, 0..n-1 — the
    descriptor array index is the sole binding authority, contract §2/§3).
    This is a gate-level lint mirroring a runtime obligation; it does not
    check anything on disk."""
    errs = []
    atts = doc.get("attachments") if isinstance(doc, dict) else None
    if not isinstance(atts, list):
        return errs
    for i, item in enumerate(atts):
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if idx != i:
            errs.append(f"$.attachments[{i}]: index {idx!r} != array position {i}")
    return errs


def _check_content_ref_index_binding(doc) -> list[str]:
    """Post-schema check, not expressible in standard JSON Schema (v3.0.0,
    contract.md §5): on a `notice` or `message` document, every attachment
    descriptor carrying `content_ref` must equal
    `f"{notice_id}.attachments/{i}"` for its own 0-based array position `i` —
    binding both the trailing index (the sole binding authority, §2) AND the
    leading `notice_id` prefix (so a descriptor cannot point at another
    notice's sidecar directory) to the array position and the enclosing
    document, respectively. Does not check anything on disk."""
    errs = []
    if not isinstance(doc, dict):
        return errs
    notice_id = doc.get("notice_id")
    message = doc.get("message")
    if isinstance(message, dict):
        atts = message.get("attachments")  # deliver-notice shape
    else:
        atts = doc.get("attachments")  # inbound-message shape
    if not isinstance(atts, list):
        return errs
    for i, item in enumerate(atts):
        if not isinstance(item, dict) or "content_ref" not in item:
            continue
        want = f"{notice_id}.attachments/{i}"
        got = item.get("content_ref")
        if got != want:
            errs.append(f"$.attachments[{i}].content_ref: {got!r} != {want!r}")
    return errs


def check_document(fixture_name: str, doc) -> list[str]:
    """Full conformance check for one document: schema validation (via the
    filename-prefix -> schema map) plus the wire-shaped post-checks that
    array-position/index binding requires (not expressible in standard JSON
    Schema). Returns a list of human-readable errors; empty list == passes.

    The result-index post-check is `result`-specific (array-position `index`
    binding, contract §2/§3) and is gated on the `result-` prefix: v2.1.0
    added `inbound-message` (`message-*`), which also carries a top-level
    `attachments[]` but has no `index` field on its descriptors at all — that
    schema's own `required`/`additionalProperties` checks already cover it,
    so running the result-only post-check there would misfire.

    The content_ref-index post-check (v3.0.0) is gated on `notice-`,
    `message-` and (v3.1.0 DRAFT) `peer-`, the artifacts that carry a
    `content_ref`-bearing descriptor keyed on a document-level `notice_id`.
    Confinement is per tree (contract.md §2 tree rules): the check binds the
    ref to the enclosing document; which `notices/` directory it resolves in
    is the tree the document was read from, which no fixture can see."""
    schema = schema_for(fixture_name)
    errs = validate(doc, schema)
    if fixture_name.startswith("result-"):
        errs.extend(_check_result_attachment_index_binding(doc))
    if fixture_name.startswith(("notice-", "message-", "peer-")):
        errs.extend(_check_content_ref_index_binding(doc))
    return errs


def schema_for(name: str):
    for prefix, fn in SCHEMA_FOR_PREFIX.items():
        if name.startswith(prefix):
            return json.loads((SCHEMA_DIR / fn).read_text())
    raise SystemExit(f"no schema mapping for fixture '{name}'")


def main() -> int:
    failures = []
    checked = 0
    for want_valid, sub in ((True, "valid"), (False, "invalid")):
        for f in sorted((HERE / sub).glob("*.json")):
            checked += 1
            errs = check_document(f.name, json.loads(f.read_text()))
            passed = not errs
            if passed != want_valid:
                if want_valid:
                    failures.append(f"[{sub}] {f.name} should PASS but failed: {errs}")
                else:
                    failures.append(f"[{sub}] {f.name} should FAIL but passed")
            print(f"  {'ok ' if passed == want_valid else 'BAD'}  {sub}/{f.name}"
                  + ("" if passed else f"  ({len(errs)} err)"))
    print(f"\n{checked} fixtures checked, {len(failures)} unexpected.")
    for fail in failures:
        print("  FAIL:", fail)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
