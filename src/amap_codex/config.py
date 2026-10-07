"""Explicit host configuration, independent of sandbox-local Codex TOML."""
from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib

ADDR_SPEC = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9.-]+")
# Codex builds whose app-server protocol has been reviewed against the
# committed schema subset (compatibility/). A deployment states which one it
# runs; any other is refused. Add a build only after re-running the
# compatibility probes against it.
SUPPORTED_CODEX_VERSIONS = ("codex-cli 0.160.1",)
SUPPORTED_CODEX = SUPPORTED_CODEX_VERSIONS[0]

@dataclass(frozen=True)
class Lane:
    name: str
    notice_dir: Path
    message_dir: Path
    claim_path: Path

@dataclass
class Config:
    instance_id: str
    self_address: str | None
    launch_argv: list[str]
    lanes: list[Lane]
    state_dir: Path
    # None: the app-server's own default, as for an agent whose Codex
    # configuration names no model.
    codex_model: str | None
    cwd: str
    sandbox: str
    approval_policy: str
    operator_instructions: Path
    outcome_dir: Path | None = None
    codex_version: str = SUPPORTED_CODEX
    poll_interval_ms: int = 1000
    max_pending_events: int = 1000
    rpc_timeout_seconds: float = 30
    turn_watchdog_seconds: float = 900
    frame_limit_bytes: int = 32 * 1024 * 1024
    shutdown_grace_seconds: float = 30
    publication_grace_seconds: float = 30
    delivery_mode: str = "tool_output"
    outcome_idempotent_replay: bool = False
    launcher_control_argv: list[str] | None = None
    deployment_id: str | None = None
    owner_domain: str | None = None
    launcher_control_timeout_seconds: float = 10
    operator_kickoff_enabled: bool = False
    trusted_config_file: Path | None = None

    @classmethod
    def load(cls, path, *, create_state=True):
        with open(path, "rb") as handle:
            doc = tomllib.load(handle)
        lanes = []
        lane_docs = doc.pop("lanes", {})
        if not isinstance(lane_docs, dict):
            raise ValueError("lanes must be a table")
        for name in ("mail", "peer"):
            values = lane_docs.get(name, {})
            if not isinstance(values, dict):
                raise ValueError(f"lanes.{name} must be a table")
            if set(values) - {"enabled", "notice_dir", "message_dir", "claim_path"}:
                raise ValueError(f"unknown setting in lanes.{name}")
            if "enabled" in values and type(values["enabled"]) is not bool:
                raise ValueError(f"lanes.{name}.enabled must be a boolean")
            paths = [values.get(k, doc.pop(f"{name}_{k}", None)) for k in ("notice_dir", "message_dir", "claim_path")]
            if values.get("enabled") is False:
                if any(v is not None for v in paths):
                    raise ValueError(f"disabled {name} lane has configured paths")
                continue
            if any(v is not None for v in paths) or name in lane_docs:
                if not all(isinstance(v, str) and v for v in paths):
                    raise ValueError(f"{name} requires notice_dir, message_dir and claim_path")
                lanes.append(Lane(name, *(Path(v) for v in paths)))
        if set(lane_docs) - {"mail", "peer"}:
            raise ValueError("unknown lane")
        for key in ("state_dir", "outcome_dir", "operator_instructions", "trusted_config_file"):
            if doc.get(key) is not None:
                doc[key] = Path(doc[key])
        doc.setdefault("self_address", None)
        doc.setdefault("codex_model", None)
        try:
            config = cls(lanes=lanes, **doc)
        except TypeError as error:
            raise ValueError(f"invalid configuration: {error}") from error
        config.validate(create_state=create_state)
        return config

    def validate(self, *, create_state=True):
        if not isinstance(self.instance_id, str) or not self.instance_id or len(self.instance_id) > 200:
            raise ValueError("instance_id must be a nonempty stable identifier")
        if not self.lanes or len({lane.name for lane in self.lanes}) != len(self.lanes):
            raise ValueError("configure at least one distinct lane")
        if self.self_address is not None and (not isinstance(self.self_address, str) or not ADDR_SPEC.fullmatch(self.self_address)):
            raise ValueError("self_address must be a bare addr-spec")
        if any(lane.name == "peer" for lane in self.lanes) and self.self_address is None:
            raise ValueError("peer lane requires self_address")
        if not isinstance(self.launch_argv, list) or not self.launch_argv or any(not isinstance(a, str) or not a or "\x00" in a for a in self.launch_argv):
            raise ValueError("launch_argv must be an explicit nonempty argument vector")
        if self.codex_version not in SUPPORTED_CODEX_VERSIONS:
            raise ValueError(f"codex_version must be one of {', '.join(SUPPORTED_CODEX_VERSIONS)}")
        if self.sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError("unsupported sandbox")
        if self.approval_policy not in {"untrusted", "on-failure", "on-request", "never"}:
            raise ValueError("unsupported approval_policy")
        if self.delivery_mode not in {"tool_output", "wake_up"}:
            raise ValueError("unsupported delivery_mode")
        if type(self.outcome_idempotent_replay) is not bool:
            raise ValueError("outcome_idempotent_replay must be an explicit boolean router agreement")
        if type(self.operator_kickoff_enabled) is not bool:
            raise ValueError("operator_kickoff_enabled must be boolean")
        if self.launcher_control_argv is not None:
            if not isinstance(self.launcher_control_argv, list) or not self.launcher_control_argv or any(not isinstance(a, str) or not a or '\x00' in a for a in self.launcher_control_argv):
                raise ValueError("launcher_control_argv must be an argument vector")
            if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', v) for v in (self.deployment_id, self.owner_domain)):
                raise ValueError("controlled launch requires deployment_id and owner_domain")
        elif self.deployment_id is not None or self.owner_domain is not None:
            raise ValueError("execution identities require launcher_control_argv")
        if not isinstance(self.cwd, str) or not Path(self.cwd).is_absolute():
            raise ValueError("cwd must be an explicit absolute sandbox path")
        if self.codex_model is not None and (not isinstance(self.codex_model, str) or not self.codex_model):
            raise ValueError("codex_model must be a nonempty model name, or absent for Codex's default")
        for key in ("poll_interval_ms", "max_pending_events", "frame_limit_bytes"):
            value = getattr(self, key)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{key} must be a positive integer")
        for key in ("rpc_timeout_seconds", "turn_watchdog_seconds", "shutdown_grace_seconds", "publication_grace_seconds", "launcher_control_timeout_seconds"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not 0 < value < float("inf"):
                raise ValueError(f"{key} must be finite and positive")
        roots = []
        for lane in self.lanes:
            if lane.name not in {"mail", "peer"}:
                raise ValueError("unknown lane")
            for root in (lane.notice_dir, lane.message_dir):
                self._directory(root)
                roots.append(root.resolve())
            if not lane.claim_path.is_absolute() or lane.claim_path.is_symlink():
                raise ValueError("claim_path must be absolute and not a symlink")
            self._directory(lane.claim_path.parent)
            if lane.claim_path.exists() and not lane.claim_path.is_file():
                raise ValueError("claim_path must be a regular file")
        if len(set(roots)) != len(roots):
            raise ValueError("lane roots must be distinct")
        if not self.state_dir.is_absolute() or self.state_dir.is_symlink():
            raise ValueError("state_dir must be absolute and not a symlink")
        if create_state:
            self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._directory(self.state_dir)
        if self.state_dir.stat().st_uid != os.getuid() or stat.S_IMODE(self.state_dir.stat().st_mode) & 0o077:
            raise ValueError("state_dir must be private (0700) and owned by supervisor")
        if any(self.state_dir.resolve().is_relative_to(root) or root.is_relative_to(self.state_dir.resolve()) for root in roots):
            raise ValueError("state_dir must be separate from inbound roots")
        if self.outcome_dir is not None:
            if not self.outcome_dir.is_absolute() or self.outcome_dir.is_symlink():
                raise ValueError("outcome_dir must be absolute and not a symlink")
            if self.outcome_dir.exists():
                self._directory(self.outcome_dir)
            else:
                self._directory(self.outcome_dir.parent)
            if any(self.outcome_dir.resolve().is_relative_to(root) for root in roots):
                raise ValueError("outcomes must be separate from inbound roots")
        if not self.operator_instructions.is_absolute() or not self.operator_instructions.is_file() or self.operator_instructions.is_symlink():
            raise ValueError("operator_instructions must be an absolute deployed regular file")
        if self.operator_instructions.stat().st_mode & 0o022:
            raise ValueError("operator_instructions must not be group/world writable")
        if self.trusted_config_file is not None:
            if not self.trusted_config_file.is_absolute() or not self.trusted_config_file.is_file() or self.trusted_config_file.is_symlink() or self.trusted_config_file.stat().st_mode & 0o022 or self.trusted_config_file.stat().st_size > 65536:
                raise ValueError('trusted_config_file must be a bounded protected host file')
            self.trusted_overrides()
        return self

    def trusted_overrides(self):
        if self.trusted_config_file is None: return None
        with self.trusted_config_file.open('rb') as stream: doc=tomllib.load(stream)
        servers=doc.get('mcp_servers',{})
        if set(servers) != {'inbox','delegation','inbox_submit'}:
            raise ValueError('trusted configuration requires exactly three AMAP MCP servers')
        for name,server in servers.items():
            tools=['submit','submit_result','peers'] if name=='inbox_submit' else ['list_messages','read_message','read_attachment']
            if server.get('enabled_tools')!=tools or server.get('required') is not True:
                raise ValueError('trusted MCP tools must use exact allowlists and required startup')
        return doc

    @staticmethod
    def _directory(path):
        if not path.is_absolute() or not path.is_dir() or path.is_symlink():
            raise ValueError(f"not an explicit existing directory: {path}")
        if not os.access(path, os.R_OK | os.X_OK):
            raise ValueError(f"directory is inaccessible: {path}")

    def fingerprint(self):
        value = asdict(self)
        # Operational polling/timeouts can change without migrating mailbox state.
        for key in ("poll_interval_ms", "max_pending_events", "rpc_timeout_seconds", "turn_watchdog_seconds", "shutdown_grace_seconds", "publication_grace_seconds", "launcher_control_timeout_seconds"):
            value.pop(key)
        # The journal records the Codex build and the model separately: a
        # move of either is audited there, not refused as a new instance.
        value.pop("codex_version")
        value.pop("codex_model")
        for key in ("launcher_control_argv", "deployment_id", "owner_domain", "trusted_config_file"):
            if value[key] is None:
                value.pop(key)
        if not value["operator_kickoff_enabled"]:
            value.pop("operator_kickoff_enabled")
        if self.trusted_config_file is not None:
            value['trusted_config_sha256']=hashlib.sha256(self.trusted_config_file.read_bytes()).hexdigest()
        value["operator_instructions_sha256"] = hashlib.sha256(self.operator_instructions.read_bytes()).hexdigest()
        raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()
