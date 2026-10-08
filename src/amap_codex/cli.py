"""Operator CLI. Status reads SQLite without invoking restart recovery."""
import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time

from .app_server import AppServerClient, AppServerDelivery
from .claims import _holder_is_live
from .config import Config
from .journal import Journal
from .supervisor import Ownership, Supervisor, atomic_json


def read_status(config):
    path = config.state_dir / "journal.sqlite3"
    base = {"instance_id": config.instance_id, "enabled_lanes": [l.name for l in config.lanes]}
    if not path.exists():
        return {**base, "state": "not_started", "thread_id": None, "pending_count": 0, "uncertain_count": 0}
    if path.is_symlink():
        raise ValueError("journal must not be a symlink")
    # URI encoding avoids treating path characters as SQL URI parameters.
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        instance = dict(db.execute("SELECT * FROM instance").fetchone())
        counts = {row["state"]: row["n"] for row in db.execute("SELECT state,COUNT(*) AS n FROM events GROUP BY state")}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='operator_runs'").fetchone():
            operators = [dict(r) for r in db.execute('SELECT * FROM operator_runs ORDER BY created_at,run_id')]
        else: operators = []
        oldest = db.execute("SELECT MIN(created_at) FROM events WHERE state='pending'").fetchone()[0]
        active = db.execute("SELECT event_id,turn_id,state FROM events WHERE state IN ('accepted','dispatching') LIMIT 1").fetchone()
        last = db.execute("SELECT MAX(accepted_at) FROM events").fetchone()[0]
        status = {**base, **instance, "counts": counts, "pending_count": counts.get("pending", 0),
                  "uncertain_count": counts.get("uncertain", 0),
                  "oldest_pending_age_seconds": None if oldest is None else max(0,time.time()-oldest),
                  "current_turn": None if active is None else dict(active), "last_successful_acceptance": last}
    finally:
        db.close()
    claims = {}
    for lane in config.lanes:
        if not lane.claim_path.exists():
            claims[lane.name] = "unclaimed"
        else:
            try:
                owner = json.loads(lane.claim_path.read_text())
                if config.launcher_control_argv:
                    claims[lane.name] = "requires_runtime_inspection"
                else:
                    claims[lane.name] = "live" if _holder_is_live(owner) else "stale"
            except (OSError, ValueError, TypeError):
                claims[lane.name] = "unreadable"
    status["operator_runs"] = operators
    status["claim_state"] = claims
    snapshot = config.state_dir / "status.json"
    if snapshot.is_file() and not snapshot.is_symlink():
        try:
            recorded = json.loads(snapshot.read_text())
            for key in ("backlog", "blocked_requests", "supervisor_pid", "updated_at"):
                if key in recorded:
                    status[key] = recorded[key]
        except (OSError, ValueError):
            pass
    return status


async def doctor_probe(config):
    status = read_status(config)
    if status.get("current_turn") or status.get("uncertain_count") or any(r['state'] in {'accepted','dispatching','uncertain'} for r in status.get('operator_runs', [])):
        raise RuntimeError("probe paused: instance has active or unresolved work")
    supervisor = Supervisor(config)
    try:
        supervisor.ownership.acquire()
        if supervisor.ownership.control:
            supervisor._record_process(os.getpid(), None)
        thread_id = await supervisor.delivery.start_or_resume(status.get('thread_id'))
        supervisor.journal = Journal(config.state_dir, config.instance_id, config.fingerprint(), config.codex_version, config.codex_model)
        supervisor.journal.bind_thread(thread_id)
        await supervisor.reconcile()
        return {"thread_id": supervisor.delivery.thread_id,
                "initialize": supervisor.delivery.client.initialize_result,
                "notice_delivered": False}
    finally:
        await supervisor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="AMAP Codex reference connector")
    parser.add_argument("--config", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="claim enabled spools and drive one persistent thread")
    commands.add_parser('guard-command', help='hold canonical guards for another controlled connector')
    commands.add_parser("status", help="read delivery state without changing it")
    doctor = commands.add_parser("doctor", help="check local paths/config; does not send a notice")
    doctor.add_argument("--probe", action="store_true", help="also initialize/start or resume app-server")
    kickoff = commands.add_parser('kickoff', help='queue privileged work on the existing controller')
    kickoff.add_argument('run_id')
    kickoff.add_argument('--instructions-file', required=True, type=Path)
    cleanup = commands.add_parser('cleanup', help='stop an orphan after acquiring the private controller lock')
    cleanup.add_argument('--stop', action='store_true', required=True)
    recovery = commands.add_parser("recover", help="audit an explicit operator disposition")
    recovery.add_argument("event_id")
    recovery.add_argument("--action", choices=["handled", "hold", "retry"], required=True)
    recovery.add_argument("--note", required=True)
    recovery.add_argument("--evidence-reference", help="positive evidence that prior dispatch caused no action")
    operator_recovery = commands.add_parser('recover-operator', help='audit an unresolved kickoff without resending')
    operator_recovery.add_argument('run_id')
    operator_recovery.add_argument('--action', choices=['handled', 'hold'], required=True)
    operator_recovery.add_argument('--note', required=True)
    migration = commands.add_parser('migrate', help='adopt the current configuration; the next start uses a new thread')
    migration.add_argument('--note', required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        config = Config.load(args.config, create_state=args.command in {"run", "recover", "guard-command", "recover-operator"})
        if args.command == "run":
            asyncio.run(Supervisor(config).run())
        elif args.command == 'guard-command':
            from .guard_command import run_guarded
            return asyncio.run(run_guarded(config))
        elif args.command == 'kickoff':
            from .operator import enqueue
            if args.instructions_file.stat().st_size > 60000:
                raise ValueError('operator instructions too large')
            print(json.dumps(enqueue(config, args.run_id, args.instructions_file.read_text())))
        elif args.command == 'cleanup':
            if not config.launcher_control_argv:
                raise ValueError('cleanup requires controlled execution')
            import fcntl
            from .lifecycle import LauncherControl
            fd = os.open(config.state_dir/'controller.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
                record = json.loads((config.state_dir/'process.json').read_text())
                if record.get('deployment_id') != config.deployment_id or record.get('owner_domain') != config.owner_domain:
                    raise ValueError('foreign execution record')
                print(json.dumps(LauncherControl(config).require_stopped(record['execution_id'], stop=True)))
            finally: os.close(fd)
        elif args.command == "status":
            print(json.dumps(read_status(config), indent=2))
        elif args.command == "doctor":
            result = {"configuration": "valid", "expected_codex_version": config.codex_version,
                      "codex_reviewed": config.codex_reviewed,
                      "fingerprint": config.fingerprint(), "enabled_lanes": [l.name for l in config.lanes],
                      "live_release_gates": "actual mounts, router, model and approval behavior require deployment verification"}
            if args.probe:
                result["probe"] = asyncio.run(doctor_probe(config))
            print(json.dumps(result, indent=2))
        elif args.command == 'migrate':
            from .journal import migrate
            with Ownership(config):
                print(json.dumps(migrate(config.state_dir, config.instance_id, config.fingerprint(), args.note)))
        elif args.command == 'recover-operator':
            with Ownership(config), Journal(config.state_dir, config.instance_id,
                                           config.fingerprint(), config.codex_version, config.codex_model) as journal:
                journal.dispose_operator(args.run_id, args.action, args.note)
                print(json.dumps({'run_id': args.run_id, 'action': args.action, 'audited': True}))
        else:
            if not args.note.strip():
                raise ValueError("a nonempty audit note is required")
            with Ownership(config), Journal(config.state_dir, config.instance_id,
                                           config.fingerprint(), config.codex_version, config.codex_model) as journal:
                if args.action == "retry":
                    if not args.evidence_reference or not args.evidence_reference.strip():
                        raise ValueError("retry requires a positive no-action evidence reference")
                    journal.retry(args.event_id, {"kind": "confirmed_not_submitted",
                        "reference": args.evidence_reference, "operator_note": args.note})
                else:
                    getattr(journal, args.action)(args.event_id, args.note)
                print(json.dumps({"event_id": args.event_id, "action": args.action, "audited": True}))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Error classes identify the failed boundary without copying sender-controlled strings.
        print(f"amap-codex: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
