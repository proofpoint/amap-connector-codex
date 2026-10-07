"""Router-agreed peer outcomes, published only from committed journal transitions."""
import json
import os
from pathlib import Path
import uuid

from .spool import SAFE_ID

class OutcomeUncertain(RuntimeError):
    pass

class OutcomePublisher:
    def __init__(self,journal,outcome_dir,*,idempotent_replay=False):
        self.journal = journal
        self.directory = Path(outcome_dir) if outcome_dir is not None else None
        self.idempotent_replay = idempotent_replay

    def publish_pending(self):
        if self.directory is None:
            return 0
        if not self.directory.is_absolute() or self.directory.is_symlink():
            raise ValueError("outcome directory must be explicit and not a symlink")
        self.directory.mkdir(mode=0o755,exist_ok=True)
        if not self.idempotent_replay and self.journal.db.execute("SELECT 1 FROM outcome_transitions WHERE publication_state='ambiguous' LIMIT 1").fetchone():
            raise OutcomeUncertain("outcome publication unresolved; router agreement or operator evidence required")
        rows = self.journal.pending_outcomes(include_ambiguous=self.idempotent_replay)
        count, blocked = 0, set()
        for row in rows:
            key = (row["tree"],row["notice_id"])
            if key in blocked:
                continue
            if row["tree"] != "peer" or not SAFE_ID.fullmatch(row["notice_id"]):
                raise ValueError("unsafe outcome identity")
            if row["publication_state"] in {"publishing", "ambiguous"} and not self.idempotent_replay:
                self.journal.publication_state(row["id"],"ambiguous")
                self.journal.operational_error("outcome publication uncertain after interrupted atomic write")
                raise OutcomeUncertain("interrupted publication may have been consumed; automatic replay requires router idempotence agreement")
            filename = f"{row['tree']}-{row['notice_id']}.json"
            final = self.directory / filename
            if final.is_symlink():
                raise ValueError("outcome destination is a symlink")
            # Retain earlier committed transition until router consumes it. Absence
            # never causes a published transition to be republished.
            if final.exists():
                blocked.add(key)
                continue
            doc = {key:row[key] for key in ("outcome","ts","tree","notice_id")}
            if row["detail"]:
                doc["detail"] = row["detail"][:200]
            payload = (json.dumps(doc,separators=(",", ":"),ensure_ascii=True)+"\n").encode()
            self.journal.publication_state(row["id"],"publishing")
            self._atomic_write(filename,payload)
            self.journal.publication_state(row["id"],"published")
            count += 1
            blocked.add(key)
        return count

    def _atomic_write(self,filename,payload):
        root_fd = os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        temp = f".{filename}.{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o644,dir_fd=root_fd)
            try:
                os.fchmod(fd,0o644)
                with os.fdopen(fd,"wb",closefd=False) as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp,filename,src_dir_fd=root_fd,dst_dir_fd=root_fd)
            os.fsync(root_fd)
        finally:
            try:
                os.unlink(temp,dir_fd=root_fd)
            except FileNotFoundError:
                pass
            os.close(root_fd)
