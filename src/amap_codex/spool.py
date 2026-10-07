"""Read-only AMAP admission. Runtime extensions are ignored, never promoted to input."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from .config import ADDR_SPEC, Lane

SAFE_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
SHA256 = re.compile(r"[a-f0-9]{64}")
CONTENT_REF = re.compile(r"[A-Za-z0-9_.-]+\.attachments/(0|[1-9][0-9]*)")
# Match the imported reader's published-document guard so admitted bodies
# remain readable through the required tool interface.
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024

@dataclass(frozen=True)
class Admission:
    event_id: str
    lane: str
    notice_id: str
    artifact_hash: str
    payload: dict
    state: str = "pending"
    detail: str = ""

class Refusal(ValueError):
    pass

class VersionRefusal(Refusal):
    pass

def event_id(instance_id, lane, notice_id):
    return hashlib.sha256(json.dumps([instance_id, lane, notice_id], separators=(",", ":")).encode()).hexdigest()

def _string(value, name, nonempty=False, pattern=None):
    if not isinstance(value, str) or (nonempty and not value) or (pattern is not None and not pattern.fullmatch(value)):
        raise Refusal(f"invalid {name}")

def _object(value, name):
    if not isinstance(value, dict):
        raise Refusal(f"{name} must be an object")
    return value

def _version(doc, name):
    # Wire version refusal precedes every schema/shape check for parsed artifacts.
    if not isinstance(doc, dict) or doc.get("contract_version") != "2":
        raise VersionRefusal(f"version refusal: {name} requires contract_version '2'")

def _strings(doc, names, prefix="", nonempty=()):
    for key in names:
        if key in doc:
            _string(doc[key], prefix + key, key in nonempty)

def _array(value, name, pattern=None, nonempty=False):
    if not isinstance(value, list):
        raise Refusal(f"{name} must be an array")
    for item in value:
        _string(item, name, nonempty, pattern)

def _attachments(doc, notice_id):
    if "attachments" not in doc:
        return
    if not isinstance(doc["attachments"], list):
        raise Refusal("attachments must be an array")
    for index, value in enumerate(doc["attachments"]):
        item = _object(value, "attachment")
        for key in ("filename", "media_type"):
            _string(item.get(key), "attachment." + key, True)
        if type(item.get("size_bytes")) is not int or item["size_bytes"] < 0:
            raise Refusal("invalid attachment.size_bytes")
        if item.get("disposition") not in {"clean", "stripped", "quarantined", "unscanned"}:
            raise Refusal("invalid attachment.disposition")
        if "sha256" in item:
            _string(item["sha256"], "attachment.sha256", pattern=SHA256)
        if "content_ref" in item:
            _string(item["content_ref"], "attachment.content_ref", pattern=CONTENT_REF)
            if item["content_ref"] != f"{notice_id}.attachments/{index}":
                raise Refusal("attachment content_ref is not bound to notice and index")
        if ("sha256" in item or "content_ref" in item) and item["disposition"] != "clean":
            raise Refusal("attachment bytes/hash require clean disposition")

def validate_notice(doc, lane, notice_id):
    _version(doc, "notice")
    _string(doc.get("notice_id"), "notice_id", True, SAFE_ID)
    if doc["notice_id"] != notice_id:
        raise Refusal("notice_id does not match filename")
    _string(doc.get("ts"), "ts", True)
    if doc.get("kind") != ("peer" if lane == "peer" else "deliver"):
        raise Refusal("kind does not match lane")
    msg = _object(doc.get("message"), "message")
    for key in ("id", "from", "mailbox"):
        _string(msg.get(key), "message." + key, True)
    for key in ("subject", "preview"):
        _string(msg.get(key), "message." + key)
    _strings(msg, ("task_id", "thread_id", "in_reply_to", "sender_standing", "provenance"), "message.", ("task_id",))
    if "references" in msg:
        _array(msg["references"], "message.references")
    if "verdict" in msg:
        verdict = _object(msg["verdict"], "message.verdict")
        _strings(verdict, ("auth", "screen", "entitlement"), "message.verdict.")
    if lane == "peer":
        _string(msg["id"], "message.id", True, SAFE_ID)
        _string(msg["from"], "message.from", True, ADDR_SPEC)
        if msg["mailbox"] != "peer":
            raise Refusal("peer message.mailbox must be peer")
        if "in_reply_to" in msg:
            _string(msg["in_reply_to"], "message.in_reply_to", True, SAFE_ID)
        if "references" in msg:
            _array(msg["references"], "message.references", SAFE_ID, True)
        if "sender_exposure" in msg:
            exp = _object(msg["sender_exposure"], "sender_exposure")
            _string(exp.get("asserted_by"), "sender_exposure.asserted_by", True, ADDR_SPEC)
            if type(exp.get("external_mail_delivered")) is not bool:
                raise Refusal("invalid sender_exposure.external_mail_delivered")
            for key in ("window_start", "window_end"):
                _string(exp.get(key), "sender_exposure." + key, True)
            _strings(exp, ("last_external_delivery_ts",), "sender_exposure.", ("last_external_delivery_ts",))
            if exp["external_mail_delivered"] and "last_external_delivery_ts" not in exp:
                raise Refusal("sender_exposure requires last_external_delivery_ts")
            if "derived_from" in exp:
                _array(exp["derived_from"], "sender_exposure.derived_from", SAFE_ID, True)
    _attachments(msg, notice_id)
    return msg

def validate_body(doc, notice_id):
    _version(doc, "body")
    _string(doc.get("notice_id"), "body.notice_id", True, SAFE_ID)
    if doc["notice_id"] != notice_id:
        raise Refusal("body.notice_id does not match filename")
    _string(doc.get("body_text"), "body_text")
    _strings(doc, ("id", "date", "from", "to", "cc", "subject"), "body.")
    _attachments(doc, notice_id)

def _read(root, filename):
    """No symlink following, including a substituted root; bounded regular files."""
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
                raise Refusal("artifact must be a bounded regular file")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                value = handle.read(MAX_ARTIFACT_BYTES + 1)
            if len(value) > MAX_ARTIFACT_BYTES:
                raise Refusal("artifact too large")
            return value, info.st_mtime
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)

def _parse(value, name):
    try:
        return json.loads(value)
    except (ValueError, UnicodeDecodeError) as error:
        raise Refusal(f"{name} is not valid JSON") from error

class Scanner:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.missing_since = {}

    def scan(self, lane: Lane, instance_id, self_address, grace=30):
        if lane.name not in {"mail", "peer"}:
            raise ValueError("unsupported lane")
        # Ignore sidecars and temporary files; malformed final notice names are refused.
        for path in sorted(lane.notice_dir.iterdir()):
            filename = path.name
            if not filename.startswith("notice-") or not filename.endswith(".json"):
                continue
            raw_id = filename[7:-5]
            safe = bool(SAFE_ID.fullmatch(raw_id))
            notice_id = raw_id if safe else "invalid-" + hashlib.sha256(filename.encode()).hexdigest()
            eid = event_id(instance_id, lane.name, notice_id)
            payload = {"event_id": eid, "lane": lane.name, "notice_id": notice_id}
            raw_notice = b""
            raw_body = b""
            state, detail = "pending", ""
            try:
                if not safe:
                    raise Refusal("unsafe notice filename identifier")
                raw_notice, modified = _read(lane.notice_dir, filename)
                notice = _parse(raw_notice, "notice")
                msg = validate_notice(notice, lane.name, notice_id)
                if lane.name == "peer":
                    if not self_address or not ADDR_SPEC.fullmatch(self_address):
                        raise Refusal("peer lane has no valid self_address")
                    if msg["from"].split("@", 1)[0] == "amap.router":
                        raise Refusal("router sender cannot task peers")
                try:
                    raw_body, _ = _read(lane.message_dir, filename)
                except FileNotFoundError:
                    now = self.clock()
                    first = self.missing_since.setdefault(eid, min(now, modified))
                    if now - first < grace:
                        continue
                    raise Refusal("body spool unavailable after publication grace")
                self.missing_since.pop(eid, None)
                body = _parse(raw_body, "body")
                validate_body(body, notice_id)
                if lane.name == "peer":
                    if body.get("to") != self_address:
                        raise Refusal("body spool to does not match self_address")
                    payload.update(peer_from=msg["from"], peer_message_id=msg["id"])
                    for key in ("in_reply_to", "references", "task_id"):
                        if key in msg:
                            payload[key] = msg[key]
            except (Refusal, OSError) as error:
                state, detail = "refused", str(error)[:200]
            # Length delimiters bind both artifacts without storing any body text.
            digest = hashlib.sha256(len(raw_notice).to_bytes(8, "big") + raw_notice + raw_body).hexdigest()
            yield Admission(eid, lane.name, notice_id, digest, payload, state, detail)

_default_scanner = Scanner()
def scan(lane, instance_id, self_address, grace=30):
    return _default_scanner.scan(lane, instance_id, self_address, grace)
