"""Local, bounded sync activity. Observability must never interrupt a data transaction."""
from __future__ import annotations

import copy
import threading
import time
import uuid

from .util import now_iso, read_json, write_json

MAX_LOGS = 1000


class SyncActivity:
    def __init__(self, path, redact=lambda value: value):
        self.path = path
        self.redact = redact
        self.lock = threading.RLock()
        saved = read_json(path, {})
        entries = saved.get("entries",[]) if isinstance(saved,dict) else []
        self.entries = [entry for entry in entries if isinstance(entry,dict) and type(entry.get("id")) is int
                        and all(isinstance(entry.get(key),str) for key in ("time","message","file","phase","run_id"))
                        and entry.get("level") in ("info","warning","error")][-MAX_LOGS:] if isinstance(entries,list) else []
        self.sequence = max((entry["id"] for entry in self.entries), default=0)
        self.progress = {"phase":"idle", "label":"等待同步", "indeterminate":True}
        self.run_id = ""
        self.last_save = 0
        self.log_error = ""

    def clean(self, value):
        return self.redact(str(value))[:2000]

    def _save(self, force=False):
        if not force and time.monotonic()-self.last_save < 1:
            return
        try:
            write_json(self.path, {"entries":self.entries})
            self.log_error = ""
            self.last_save = time.monotonic()
        except OSError as exc:
            self.log_error = "同步日志暂时无法保存：" + self.clean(exc)

    def log(self, level, message, *, phase="", file="", force=False):
        with self.lock:
            self.sequence += 1
            self.entries.append({"id":self.sequence,"time":now_iso(),"level":level,
                "message":self.clean(message),"phase":phase,"file":self.clean(file),"run_id":self.run_id})
            self.entries = self.entries[-MAX_LOGS:]
            self._save(force)

    def begin(self):
        with self.lock:
            self.run_id = uuid.uuid4().hex
            self.progress = {}
            self.notify(phase="connecting", label="检查同步连接", reset=True)

    def notify(self, *, phase=None, label=None, reset=False, bytes_delta=0, file_complete=None, **values):
        with self.lock:
            if reset or phase is not None and phase != self.progress.get("phase"):
                self.progress = {"phase":phase,"label":label,"done_files":0,"total_files":None,
                    "bytes_done":0,"bytes_total":None,"current_file":"","started_at":now_iso()}
                if label:
                    self.log("info", label, phase=phase or "")
            elif label:
                self.progress["label"] = label
            for key, value in values.items():
                if key in ("done_files","total_files","bytes_done","bytes_total","current_file"):
                    self.progress[key] = self.clean(value) if key == "current_file" else value
            self.progress["bytes_done"] = self.progress.get("bytes_done", 0) + bytes_delta
            self.progress["updated_at"] = now_iso()
            if file_complete:
                self.log("info", file_complete, phase=self.progress.get("phase", ""), file=self.progress.get("current_file", ""))

    def finish(self, phase, message):
        with self.lock:
            self.progress.update(phase=phase,label=self.clean(message),current_file="",done_files=0,total_files=None,
                                 bytes_done=0,bytes_total=None,updated_at=now_iso())
            self.log("error" if phase == "error" else "warning" if phase in ("paused","waiting") else "info", message, phase=phase, force=True)

    def snapshot(self):
        with self.lock:
            progress = copy.deepcopy(self.progress)
            total = progress.get("bytes_total")
            if total is not None and total > 0:
                percent = min(100, int(progress.get("bytes_done", 0)*100/total))
            elif progress.get("total_files") is not None:
                percent = min(100, int(progress.get("done_files", 0)*100/max(1,progress["total_files"])))
                if progress["total_files"] == 0:
                    percent = 100
            else:
                percent = None
            if progress.get("phase") == "complete":
                percent = 100
            elif progress.get("phase") == "apply" and percent is not None:
                percent = min(99, percent)
            elif progress.get("phase") in ("idle","waiting","paused","error"):
                percent = None
            progress.update(percent=percent,indeterminate=percent is None,run_id=self.run_id)
            return progress

    def logs(self, limit=50):
        if type(limit) is not int or not 1 <= limit <= MAX_LOGS:
            raise ValueError("日志数量必须是 1–1000")
        with self.lock:
            return {"items":copy.deepcopy(self.entries[-limit:][::-1]),"retained":len(self.entries),
                "max_entries":MAX_LOGS,"error":self.log_error}

    def flush(self):
        with self.lock:
            self._save(force=True)
