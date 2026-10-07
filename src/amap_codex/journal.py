"""Durable metadata-only delivery state; acceptance and execution are distinct."""
import json
import os
from pathlib import Path
import sqlite3
import time
from datetime import datetime, timezone

class IntegrityConflict(RuntimeError):
    pass

class StateConflict(RuntimeError):
    pass

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

class Journal:
    def __init__(self, path, instance_id, fingerprint, codex_version, *, clock=time.time):
        path = Path(path)
        if path.is_dir():
            path = path / "journal.sqlite3"
        if path.is_symlink():
            raise ValueError("journal must not be a symlink")
        self.path, self.instance_id, self.clock = path, instance_id, clock
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS instance (
          instance_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, codex_version TEXT NOT NULL,
          thread_id TEXT, last_error TEXT);
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, lane TEXT NOT NULL,
          notice_id TEXT NOT NULL, artifact_hash TEXT NOT NULL, payload TEXT NOT NULL,
          state TEXT NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL,
          updated_at REAL NOT NULL, eligible_at REAL NOT NULL, turn_id TEXT,
          execution_status TEXT, accepted_at REAL,
          UNIQUE(instance_id,lane,notice_id));
        CREATE TABLE IF NOT EXISTS attempts (
          event_id TEXT NOT NULL REFERENCES events(event_id), number INTEGER NOT NULL,
          rpc_id TEXT NOT NULL, started_at REAL NOT NULL, state TEXT NOT NULL,
          turn_id TEXT, detail TEXT, PRIMARY KEY(event_id,number));
        CREATE TABLE IF NOT EXISTS outcome_transitions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL REFERENCES events(event_id),
          outcome TEXT NOT NULL, ts TEXT NOT NULL, tree TEXT NOT NULL,
          notice_id TEXT NOT NULL, detail TEXT NOT NULL,
          publication_state TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, ts TEXT NOT NULL,
          action TEXT NOT NULL, note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operator_runs (
          run_id TEXT PRIMARY KEY, instruction_hash TEXT NOT NULL,
          state TEXT NOT NULL, rpc_id TEXT, turn_id TEXT, execution_status TEXT,
          created_at REAL NOT NULL, updated_at REAL NOT NULL);
        ''')
        try:
            with self._transaction():
                row = self.db.execute("SELECT * FROM instance").fetchone()
                if row is None:
                    self.db.execute("INSERT INTO instance(instance_id,fingerprint,codex_version) VALUES(?,?,?)", (instance_id, fingerprint, codex_version))
                elif (row["instance_id"], row["fingerprint"], row["codex_version"]) != (instance_id, fingerprint, codex_version):
                    raise IntegrityConflict("instance/configuration/version changed; explicit state migration required")
                # A committed dispatch before a crash can never be assumed unsent.
                self.db.execute("UPDATE operator_runs SET state='uncertain' WHERE state='dispatching'")
                for event in self.db.execute("SELECT event_id FROM events WHERE state='dispatching'").fetchall():
                    self._set(event["event_id"], "uncertain", "restart during dispatch: acceptance unresolved")
                    self._attempt_state(event["event_id"], "uncertain", "restart during dispatch")
                    self._outcome(event["event_id"], "held", "restart during dispatch: acceptance unresolved")
        except BaseException:
            self.db.close()
            raise

    class _Transaction:
        def __init__(self, db): self.db = db
        def __enter__(self): self.db.execute("BEGIN IMMEDIATE")
        def __exit__(self, kind, error, traceback):
            self.db.execute("ROLLBACK" if kind else "COMMIT")
    def _transaction(self): return self._Transaction(self.db)
    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    @property
    def thread_id(self):
        return self.db.execute("SELECT thread_id FROM instance WHERE instance_id=?", (self.instance_id,)).fetchone()[0]

    def bind_thread(self, thread_id):
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("thread_id is required")
        with self._transaction():
            old = self.thread_id
            if old is not None and old != thread_id:
                raise IntegrityConflict("journal is bound to a different thread")
            self.db.execute("UPDATE instance SET thread_id=? WHERE instance_id=?", (thread_id, self.instance_id))

    def _event(self, event_id):
        row = self.db.execute("SELECT * FROM events WHERE event_id=? AND instance_id=?", (event_id,self.instance_id)).fetchone()
        if row is None: raise KeyError(event_id)
        return row

    @staticmethod
    def _dict(row):
        if row is None: return None
        value = dict(row)
        if "payload" in value: value["payload"] = json.loads(value["payload"])
        return value

    def event(self, event_id): return self._dict(self._event(event_id))

    def admit(self, admission):
        if admission.state not in {"pending", "refused"}:
            raise ValueError("invalid admission state")
        # Do not persist arbitrary caller-supplied sender data.
        allowed = {"event_id", "lane", "notice_id", "peer_from", "peer_message_id", "in_reply_to", "references", "task_id"}
        if set(admission.payload) - allowed:
            raise ValueError("event payload contains non-metadata fields")
        conflict = False
        with self._transaction():
            prior = self.db.execute("SELECT * FROM events WHERE instance_id=? AND lane=? AND notice_id=?", (self.instance_id,admission.lane,admission.notice_id)).fetchone()
            if prior is not None:
                conflict = prior["artifact_hash"] != admission.artifact_hash or prior["event_id"] != admission.event_id
                if conflict:
                    detail = "publication integrity conflict: artifacts changed for recorded notice"
                    self._audit(prior["event_id"], "integrity_conflict", detail)
                    self.db.execute("UPDATE instance SET last_error=?", (detail,))
                    if prior["state"] in {"pending", "dispatching", "uncertain"}:
                        self._set(prior["event_id"], "uncertain", detail)
                        self._outcome(prior["event_id"], "held", detail)
                inserted = False
            else:
                now = self.clock()
                self.db.execute("INSERT INTO events(event_id,instance_id,lane,notice_id,artifact_hash,payload,state,detail,created_at,updated_at,eligible_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (admission.event_id,self.instance_id,admission.lane,admission.notice_id,admission.artifact_hash,json.dumps(admission.payload,sort_keys=True),admission.state,admission.detail[:200],now,now,now))
                if admission.state == "refused": self._outcome(admission.event_id,"refused",admission.detail)
                inserted = True
        if conflict: raise IntegrityConflict("publication integrity conflict for " + admission.event_id)
        return inserted

    def next_pending(self):
        if self.operator_blocked():
            return None
        if self.db.execute("SELECT 1 FROM events WHERE state IN ('dispatching','accepted','uncertain') LIMIT 1").fetchone():
            return None
        return self._dict(self.db.execute("SELECT * FROM events WHERE state='pending' AND eligible_at<=? ORDER BY created_at,event_id LIMIT 1", (self.clock(),)).fetchone())

    def begin_attempt(self, event_id, rpc_id):
        with self._transaction():
            row = self._event(event_id)
            if not self.thread_id: raise StateConflict("bind thread before dispatch")
            if self.operator_blocked(): raise StateConflict('operator work active or uncertain')
            if row["state"] != "pending" or row["eligible_at"] > self.clock():
                raise StateConflict("event is not eligible")
            if self.db.execute("SELECT 1 FROM events WHERE state IN ('dispatching','accepted','uncertain') LIMIT 1").fetchone():
                raise StateConflict("instance already active or uncertain")
            number = self.db.execute("SELECT COALESCE(MAX(number),0)+1 FROM attempts WHERE event_id=?", (event_id,)).fetchone()[0]
            self.db.execute("INSERT INTO attempts(event_id,number,rpc_id,started_at,state) VALUES(?,?,?,?,?)", (event_id,number,json.dumps(rpc_id),self.clock(),"dispatching"))
            self._set(event_id,"dispatching", "")
            return number

    def _set(self,event_id,state,detail):
        self.db.execute("UPDATE events SET state=?,detail=?,updated_at=? WHERE event_id=?", (state,detail[:200],self.clock(),event_id))

    def _attempt_state(self,event_id,state,detail="",turn_id=None):
        self.db.execute("UPDATE attempts SET state=?,detail=?,turn_id=COALESCE(?,turn_id) WHERE event_id=? AND number=(SELECT MAX(number) FROM attempts WHERE event_id=?)", (state,detail[:200],turn_id,event_id,event_id))

    def accept(self,event_id,turn_id):
        if not isinstance(turn_id,str) or not turn_id: raise ValueError("turn_id required")
        with self._transaction():
            row = self._event(event_id)
            if row["state"] in {"accepted","finished"} and row["turn_id"] == turn_id: return
            if row["state"] not in {"dispatching","uncertain"}: raise StateConflict("acceptance is not applicable")
            self._set(event_id,"accepted","app-server accepted event into bound thread")
            self.db.execute("UPDATE events SET turn_id=?,accepted_at=? WHERE event_id=?", (turn_id,self.clock(),event_id))
            self._attempt_state(event_id,"accepted",turn_id=turn_id)
            self._outcome(event_id,"delivered","app-server acceptance; execution completion is separate")

    def unsent(self,event_id,detail,*,evidence="transport_not_written",max_attempts=3):
        if evidence not in {"transport_not_written", "confirmed_not_submitted"}:
            raise ValueError("positive proof of non-submission required")
        with self._transaction():
            if self._event(event_id)["state"] != "dispatching": raise StateConflict("no dispatching attempt")
            number = self.db.execute("SELECT MAX(number) FROM attempts WHERE event_id=?", (event_id,)).fetchone()[0]
            self._attempt_state(event_id,"unsent",detail)
            self._outcome(event_id,"inject_failed","proven before acceptance: " + detail)
            if number >= max_attempts:
                self._set(event_id,"uncertain","proven unsent; bounded retries exhausted, operator hold")
                self._outcome(event_id,"held","proven unsent; bounded retries exhausted")
            else:
                self._set(event_id,"pending",detail)
                self.db.execute("UPDATE events SET eligible_at=? WHERE event_id=?", (self.clock()+min(60,2**(number-1)),event_id))

    def uncertain(self,event_id,detail):
        with self._transaction():
            if self._event(event_id)["state"] not in {"dispatching","uncertain"}: raise StateConflict("uncertainty requires unresolved dispatch")
            self._set(event_id,"uncertain",detail)
            self._attempt_state(event_id,"uncertain",detail)
            self._outcome(event_id,"held","acceptance unresolved: " + detail)

    def finish(self,event_id,status):
        if status not in {"completed","failed","interrupted"}: raise ValueError("invalid terminal execution status")
        with self._transaction():
            row = self._event(event_id)
            if row["state"] == "finished" and row["execution_status"] == status: return
            if row["state"] != "accepted": raise StateConflict("terminal execution requires acceptance")
            self._set(event_id,"finished","")
            self.db.execute("UPDATE events SET execution_status=? WHERE event_id=?", (status,event_id))
            self._attempt_state(event_id,"finished")

    def recovery_events(self):
        return [self._dict(row) for row in self.db.execute("SELECT * FROM events WHERE state IN ('dispatching','uncertain','accepted') ORDER BY created_at")]

    def _audit(self,event_id,action,note):
        self.db.execute("INSERT INTO audit(event_id,ts,action,note) VALUES(?,?,?,?)", (event_id,utc_now(),action,note[:1000]))

    def handled(self,event_id,note):
        if not isinstance(note,str) or not note.strip(): raise ValueError("audit note required")
        with self._transaction():
            if self._event(event_id)["state"] not in {"pending","uncertain","refused","accepted"}: raise StateConflict("event cannot be marked handled")
            self._set(event_id,"finished","operator marked handled")
            self.db.execute("UPDATE events SET execution_status='operator_handled' WHERE event_id=?", (event_id,))
            self._audit(event_id,"handled",note)

    def hold(self,event_id,note):
        if not isinstance(note,str) or not note.strip(): raise ValueError("audit note required")
        with self._transaction():
            if self._event(event_id)["state"] not in {"pending","uncertain","refused"}: raise StateConflict("event cannot be held")
            self._set(event_id,"uncertain","operator hold: " + note)
            self._audit(event_id,"hold",note)
            self._outcome(event_id,"held","operator hold: " + note)

    def retry(self,event_id,evidence):
        if not isinstance(evidence,dict) or evidence.get("kind") not in {"transport_not_written","confirmed_not_submitted"} or not isinstance(evidence.get("reference"),str) or not evidence["reference"].strip():
            raise ValueError("retry requires positive non-submission evidence and reference")
        with self._transaction():
            if self._event(event_id)["state"] != "uncertain": raise StateConflict("only unresolved work can be retried")
            self._audit(event_id,"retry",json.dumps(evidence,sort_keys=True))
            self._set(event_id,"pending","positive evidence of no prior submission")
            self.db.execute("UPDATE events SET eligible_at=? WHERE event_id=?", (self.clock(),event_id))

    def operational_error(self,detail):
        with self._transaction():
            self.db.execute("UPDATE instance SET last_error=?", (str(detail)[:200],))

    def operator_blocked(self):
        return self.db.execute("SELECT 1 FROM operator_runs WHERE state IN ('dispatching','accepted','uncertain') LIMIT 1").fetchone() is not None

    def operator_runs(self, *, recovery=False):
        sql = "SELECT * FROM operator_runs"
        if recovery: sql += " WHERE state IN ('accepted','uncertain')"
        return [dict(r) for r in self.db.execute(sql + ' ORDER BY created_at,run_id')]

    def admit_operator(self, run_id, instruction_hash):
        with self._transaction():
            old = self.db.execute('SELECT instruction_hash FROM operator_runs WHERE run_id=?', (run_id,)).fetchone()
            if old:
                if old[0] != instruction_hash: raise IntegrityConflict('operator run content changed')
                return
            now = self.clock()
            self.db.execute('INSERT INTO operator_runs(run_id,instruction_hash,state,created_at,updated_at) VALUES(?,?,?,?,?)',
                            (run_id,instruction_hash,'pending',now,now))

    def begin_operator(self, run_id, rpc_id):
        with self._transaction():
            if not self.thread_id or self.operator_blocked() or self.db.execute("SELECT 1 FROM events WHERE state IN ('dispatching','accepted','uncertain') LIMIT 1").fetchone():
                raise StateConflict('instance already active or uncertain')
            cursor = self.db.execute("UPDATE operator_runs SET state='dispatching',rpc_id=?,updated_at=? WHERE run_id=? AND state='pending'", (str(rpc_id),self.clock(),run_id))
            if cursor.rowcount != 1: raise StateConflict('operator run is not pending')

    def operator_result(self, run_id, state, *, turn_id=None, execution_status=None):
        with self._transaction():
            row = self.db.execute('SELECT * FROM operator_runs WHERE run_id=?', (run_id,)).fetchone()
            if row is None: raise KeyError(run_id)
            allowed = {'dispatching': {'accepted','uncertain','unsent'},
                       'uncertain': {'accepted','uncertain'},
                       'accepted': {'accepted','finished'}, 'finished': {'finished'}}
            if state not in allowed.get(row['state'], set()): raise StateConflict('invalid operator transition')
            if state in {'accepted','finished'} and not (turn_id or row['turn_id']):
                raise StateConflict('operator acceptance requires a turn')
            self.db.execute('UPDATE operator_runs SET state=?,turn_id=COALESCE(?,turn_id),execution_status=COALESCE(?,execution_status),updated_at=? WHERE run_id=?',
                (state,turn_id,execution_status,self.clock(),run_id))

    def dispose_operator(self, run_id, action, note):
        if action not in {'handled', 'hold'} or not isinstance(note, str) or not note.strip():
            raise ValueError('operator disposition requires handled/hold and an audit note')
        with self._transaction():
            row = self.db.execute('SELECT state FROM operator_runs WHERE run_id=?', (run_id,)).fetchone()
            if row is None: raise KeyError(run_id)
            if row['state'] not in {'accepted', 'uncertain'}: raise StateConflict('operator run is not unresolved')
            if action == 'handled':
                self.db.execute("UPDATE operator_runs SET state='finished',execution_status='operator_handled',updated_at=? WHERE run_id=?", (self.clock(), run_id))
            self._audit('operator:' + run_id, action, note)

    def status(self):
        counts = {row["state"]: row["n"] for row in self.db.execute("SELECT state,COUNT(*) AS n FROM events GROUP BY state")}
        pending = self.db.execute("SELECT MIN(created_at) FROM events WHERE state='pending'").fetchone()[0]
        active = self.db.execute("SELECT event_id,turn_id,state FROM events WHERE state IN ('accepted','dispatching') LIMIT 1").fetchone()
        accepted = self.db.execute("SELECT MAX(accepted_at) FROM events").fetchone()[0]
        instance = dict(self.db.execute("SELECT * FROM instance").fetchone())
        return {**instance,"counts":counts,"pending_count":counts.get("pending",0),"uncertain_count":counts.get("uncertain",0),"oldest_pending_age_seconds":None if pending is None else max(0,self.clock()-pending),"current_turn":None if active is None else dict(active),"last_successful_acceptance":accepted}

    def _outcome(self,event_id,outcome,detail):
        row = self._event(event_id)
        if row["lane"] != "peer": return
        last = self.db.execute("SELECT outcome FROM outcome_transitions WHERE event_id=? ORDER BY id DESC LIMIT 1", (event_id,)).fetchone()
        if last is not None and last[0] == outcome: return
        self.db.execute("INSERT INTO outcome_transitions(event_id,outcome,ts,tree,notice_id,detail) VALUES(?,?,?,?,?,?)", (event_id,outcome,utc_now(),row["lane"],row["notice_id"],detail[:200]))

    def pending_outcomes(self, *, include_ambiguous=False):
        states = ("pending", "publishing", "ambiguous") if include_ambiguous else ("pending", "publishing")
        placeholders = ",".join("?" for _ in states)
        return [dict(row) for row in self.db.execute(f"SELECT * FROM outcome_transitions WHERE publication_state IN ({placeholders}) ORDER BY id", states)]

    def publication_state(self,transition_id,state):
        if state not in {"publishing","published","ambiguous"}: raise ValueError("invalid publication state")
        with self._transaction():
            self.db.execute("UPDATE outcome_transitions SET publication_state=? WHERE id=?", (state,transition_id))
