"""Tiny in-process background-job registry. A job has an id, a state (queued/running/done/error), progress 0..1,
a message and a result. The front end polls GET /api/jobs/{id} once per second."""
import itertools, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor

class Cancelled(Exception):
    """Raised inside a job's work (from job.update) once the job has been cancelled."""

class Job:
    def __init__(self, jid, kind):
        self.id, self.kind = jid, kind
        self.state, self.progress, self.message = "queued", 0.0, "Queued"
        self.result, self.error = None, None
        self.created = time.time()
        self.cancelled = False

    def cancel(self):
        """Ask the job to stop: a queued job never starts, a running one stops at its next update()."""
        self.cancelled = True

    def update(self, progress=None, message=None):
        if self.cancelled:
            raise Cancelled("Cancelled: a newer request replaced this one.")
        if progress is not None:
            self.progress = float(min(max(progress, 0.0), 1.0))
        if message is not None:
            self.message = message

    def public(self):
        d = {"id": self.id, "kind": self.kind, "state": self.state, "progress": round(self.progress, 3), "message": self.message}
        if self.state == "done":
            d["result"] = self.result
        if self.state == "error":
            d["error"] = self.error
        return d

class Jobs:
    def __init__(self, workers=2, keep=200, lanes=None):
        """lanes: {kind: workers} gives those job kinds their own worker threads, so long background work (Monte Carlo
        ranges, tile cutting) never makes an interactive job (area, responders, access) wait in the queue behind it."""
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._lanes = {k: ThreadPoolExecutor(max_workers=n, thread_name_prefix=f"job-{k}") for k, n in (lanes or {}).items()}
        self._jobs, self._ids, self._lock, self._keep = {}, itertools.count(1), threading.Lock(), keep

    def submit(self, kind, fn, *args, **kw):
        """Run fn(job, *args, **kw) in the background; fn reports with job.update(progress, message) and returns the
        result (must be JSON-friendly). Exceptions become state 'error' with a short message."""
        with self._lock:
            job = Job(f"{kind}-{next(self._ids)}", kind)
            self._jobs[job.id] = job
            for old in sorted(self._jobs.values(), key=lambda j: j.created)[: max(0, len(self._jobs) - self._keep)]:
                if old.state in ("done", "error"):
                    self._jobs.pop(old.id, None)

        def run():
            if job.cancelled:
                job.state, job.error, job.message = "error", "Cancelled: a newer request replaced this one.", "Cancelled"
                return
            job.state, job.message = "running", "Starting"
            try:
                job.result = fn(job, *args, **kw)
                job.progress, job.state, job.message = 1.0, "done", "Done"
            except Cancelled as ex:
                job.state, job.error, job.message = "error", str(ex), "Cancelled"
            except Exception as ex:                      # report to the poller, never crash the worker
                job.state, job.error = "error", getattr(ex, "detail", None) or str(ex)[:300] or ex.__class__.__name__
                job.message = "Failed"
                traceback.print_exc()
        self._lanes.get(kind, self._pool).submit(run)
        return job

    def get(self, jid):
        return self._jobs.get(jid)
