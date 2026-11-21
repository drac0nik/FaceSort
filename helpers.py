"""Helper processes (1.45): FaceSort's heavy background work runs in separate processes, so it
can use every core and never slows the window down.

Why processes: Python runs one thread's code at a time, so background threads - however low
their priority - took turns with the window (the pauses in speed.log on big libraries). A
helper process has its own Python, so it runs truly alongside the window, on its own core.

The pool
- One graphics card helper ("gpu"): everything that runs a model (what's in photos, the photo
  descriptions, face details, people's bodies). A graphics card does one job at a time best, so
  one helper owns it; it decodes photos on several threads to keep the card busy.
- Several CPU helpers ("cpu"): the work that splits by photo or that just reads a lot (face
  clarity, duplicates, where photos go, suggestions). How many: Settings > Scanning / CPU / GPU (by
  default half the cores, at most 6).
- One queue, by priority: what you just did first (NOW), then SOON, then housekeeping (IDLE),
  which also eases off while you're using the window.
- Writing: every process writes through dbgate's write gate, and the gates share one lock, so
  there's still only one writer at a time (the window first) - never "database is locked".
- A helper loads its models once and keeps them for the next job. Helpers are started when
  first needed, and end with FaceSort (or on their own if FaceSort is gone).

This module has no Qt in it (the helpers import it). The window side is ProcessWorker in
ui_common, which looks just like the old background thread (Worker) to the rest of FaceSort.
"""
import heapq
import importlib
import itertools
import multiprocessing as mp
import os
import pickle
import queue as _queue
import sys
import threading
import time
import traceback

ROLE_ENV = "FACESORT_HELPER"            # "gpu" / "cpu" in a helper; unset in the window
NOW, SOON, IDLE = 0, 1, 2               # priorities
MAX_CPU_HELPERS = 6
GPU_CRASHES_MAX = 2                     # (1.49.2) a job that crashed the GPU helper this often runs on the CPU
                                        # (1.51.1: after one crash it tries the card again without FP16)
GPU_CRASH_DAYS = 30                     # (1.51.1) remembered this long (was: until the next version)
UI_QUIET = 1.5                          # seconds after your last click / key before housekeeping goes full speed


def _app_version():
    try:
        from ui_common import APP_VERSION      # (window side only)
        return APP_VERSION
    except Exception:
        return "?"


def role():
    return os.environ.get(ROLE_ENV)


def default_cpu_helpers(cores=None):
    cores = cores or os.cpu_count() or 2
    return max(1, min(MAX_CPU_HELPERS, cores // 2))


def cpu_helpers(settings):
    n = (settings or {}).get("cpu_helpers", 0)
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    return max(1, min(16, n)) if n > 0 else default_cpu_helpers()


def enabled(settings):
    """Helpers on? (Settings > Scanning / CPU / GPU; FACESORT_NO_HELPERS=1 turns them off - the tests do.)"""
    if os.environ.get("FACESORT_NO_HELPERS") == "1" or role() is not None:
        return False
    return (settings or {}).get("helpers", "on") != "off"


def onnx_providers(use_gpu=True):
    """ONNX Runtime providers for a model in this process: the graphics card in the GPU helper
    (and in the window, as before), never in a CPU helper (they'd queue up on the card)."""
    import onnxruntime as ort
    if role() == "cpu" or not use_gpu:
        return ["CPUExecutionProvider"]
    avail = ort.get_available_providers()
    return [p for p in ("CUDAExecutionProvider", "DmlExecutionProvider") if p in avail] + ["CPUExecutionProvider"]


def session_options(provs=None):
    """SessionOptions that suit the process: a CPU helper uses only its share of the cores; on
    DirectML memory patterns are off and execution sequential (what DirectML needs)."""
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    n = int(os.environ.get("FACESORT_HELPER_THREADS", "0") or 0)
    if n:
        so.intra_op_num_threads = n
    if provs and provs[0] == "DmlExecutionProvider":
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return so


def decode_threads():
    """Threads that decode photos for one model job (decoding is most of the time; the graphics
    card waits for pictures otherwise)."""
    if role() == "gpu":
        return max(2, min(6, (os.cpu_count() or 4) // 3))
    return 1


def decoded(items, load, workers=None, ahead=16):
    """[load(item)] in order, decoded on a few threads ahead of the caller (load returns the
    picture or raises; a failure comes back as the exception)."""
    workers = workers or decode_threads()
    if workers <= 1:
        for it in items:
            try:
                yield it, load(it)
            except Exception as e:
                yield it, e
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="decode") as ex:
        pending = []
        src = iter(items)
        for it in itertools.islice(src, ahead):
            pending.append((it, ex.submit(load, it)))
        while pending:
            it, fut = pending.pop(0)
            nxt = next(src, None)
            if nxt is not None:
                pending.append((nxt, ex.submit(load, nxt)))
            try:
                yield it, fut.result()
            except Exception as e:
                yield it, e


# =========================================================================== in the helper
def _lower_priority():
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            # (1.49.2) types spelled out: the handle used to be cut to 32 bits and the call failed
            k.GetCurrentProcess.restype = wintypes.HANDLE
            k.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)  # below normal
        else:
            os.nice(5)
    except Exception:
        pass


def _resolve(name):
    mod, fn = name.split(":")
    return getattr(importlib.import_module(mod), fn)


def _watch_parent():
    """End this helper if FaceSort itself is gone (closed, or crashed)."""
    parent = mp.parent_process()
    while True:
        time.sleep(2)
        if parent is not None and not parent.is_alive():
            os._exit(0)


def helper_main(role_, slot, task_q, result_q, cancel, ui_active, xlock, xui, xowner, threads):
    os.environ[ROLE_ENV] = role_
    os.environ["FACESORT_HELPER_THREADS"] = str(threads)
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[v] = str(threads)
    _lower_priority()
    threading.Thread(target=_watch_parent, daemon=True).start()
    import dbgate
    dbgate.share(xlock, xui, xowner)
    import store
    conn, conn_dir = None, None
    result_q.put((None, "ready", (role_, slot, os.getpid())))
    while True:
        try:
            task = task_q.get()
        except (EOFError, OSError):
            break
        if task is None:
            break
        tid, func, args, kw, pri, data_dir = task[:6]
        opts = task[6] if len(task) > 6 else {}
        if opts.get("mk"):
            import cryptbox
            cryptbox.set_master(opts["mk"])
            import fsx
            fsx.CACHE_BYTES = 300 << 20       # (1.58) a helper keeps few readable copies of encrypted photos
        if opts.get("nofp16"):
            os.environ["FACESORT_NO_FP16"] = "1"
        else:
            os.environ.pop("FACESORT_NO_FP16", None)
        cancel.value = 0
        if data_dir != conn_dir:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn, conn_dir = None, data_dir
            if data_dir:
                os.environ["FACESORT_DATA"] = data_dir
        last = [0.0]

        def progress(d, t=0, m="", tid=tid, pri=pri, last=last):
            now = time.monotonic()
            if now - last[0] > 0.1 or (t and d >= t):
                last[0] = now
                result_q.put((tid, "progress", (int(d or 0), int(t or 0), str(m or ""))))
            if pri >= IDLE and ui_active.value:
                time.sleep(0.02)              # you're using the window: housekeeping eases off

        def cancelled():
            return cancel.value != 0
        try:
            if conn is None:
                conn = store.connect()
            res = _resolve(func)(conn, *args, progress=progress, cancelled=cancelled, **kw)
            if conn.in_transaction:
                conn.commit()
            try:
                blob = pickle.dumps(res, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception as e:
                raise RuntimeError(f"the result of {func} can't be sent back: {e}")
            result_q.put((tid, "ok", blob))
        except BaseException:
            tb = traceback.format_exc()
            try:
                if conn is not None and conn.in_transaction:
                    conn.rollback()
            except Exception:
                pass
            result_q.put((tid, "failed", tb))
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


# =========================================================================== in the window
class _Helper:
    def __init__(self, role_, slot, proc, task_q, cancel):
        self.role, self.slot, self.proc, self.task_q, self.cancel = role_, slot, proc, task_q, cancel
        self.tid = None                    # the task it's doing
        self.pid = None
        self.ready = False


class Pool:
    def __init__(self):
        self.ctx = mp.get_context("spawn")
        self.xlock = self.ctx.Lock()
        self.xui = self.ctx.Value("i", 0)
        self.xowner = self.ctx.Value("i", 0)
        self.ui_active = self.ctx.Value("b", 0)
        self.result_q = self.ctx.Queue()
        self.helpers = []
        self.waiting = []                  # heap of (priority, seq, tid)
        self.tasks = {}                    # tid -> {"msg": (func, args, kw, pri, role, data_dir), "cb", "helper"}
        self.seq = itertools.count(1)
        self.n_cpu, self.use_gpu = default_cpu_helpers(), True
        self.last_touch = 0.0
        self.closed = False
        self.log = lambda m: None
        import dbgate
        dbgate.share(self.xlock, self.xui, self.xowner)

    # ---- settings
    def configure(self, n_cpu, use_gpu=True):
        self.n_cpu, self.use_gpu = max(1, int(n_cpu)), bool(use_gpu)
        extra = [h for h in self.helpers if h.role == "cpu" and h.tid is None][self.n_cpu:]
        for h in extra:                    # fewer CPU helpers now: idle ones end
            self._end(h)

    def touch(self):
        """You clicked or typed: housekeeping eases off for a moment."""
        self.last_touch = time.monotonic()

    # ---- jobs
    # ---- (1.49.2) jobs that crash the graphics card helper run on the CPU instead
    def _crash_file(self):
        import store
        return os.path.join(store.data_dir(), "gpu_crashes.json")

    def gpu_crashes(self):
        """{job: crashes} (the last GPU_CRASH_DAYS days)."""
        if getattr(self, "_crashes", None) is None:
            self._crashes, self._crash_at = {}, {}
            try:
                import json
                with open(self._crash_file(), encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.get("jobs", {}).items():
                    n, at = (v[0], v[1]) if isinstance(v, list) else (int(v), time.time())
                    if time.time() - at < GPU_CRASH_DAYS * 86400:
                        self._crashes[k], self._crash_at[k] = int(n), at
            except Exception:
                pass
        return self._crashes

    def _note_crash(self, func):
        c = self.gpu_crashes()
        c[func] = c.get(func, 0) + 1
        if getattr(self, "_crash_at", None) is None:
            self._crash_at = {}
        self._crash_at[func] = time.time()
        try:
            import json
            with open(self._crash_file(), "w", encoding="utf-8") as f:
                json.dump({"jobs": {k: [n, self._crash_at.get(k, time.time())] for k, n in c.items()}}, f)
        except Exception:
            pass
        return c[func]

    def gpu_safe(self, func):
        return self.gpu_crashes().get(func, 0) < GPU_CRASHES_MAX

    def submit(self, func, args=(), kw=None, role_="cpu", priority=IDLE, cb=None):
        tid = next(self.seq)
        opts = {}
        if role_ == "gpu" and not self.gpu_safe(func):
            role_ = "cpu"                  # (it crashed the graphics card helper: the CPU does it, slower but sure)
        elif role_ == "gpu" and self.gpu_crashes().get(func, 0):
            opts["nofp16"] = True          # (crashed once: the card again, without FP16)
        import store
        self.tasks[tid] = {"msg": (func, tuple(args), dict(kw or {}), priority, role_, store.data_dir()),
                           "cb": cb, "helper": None, "opts": opts}
        heapq.heappush(self.waiting, (priority, tid, tid))
        self._dispatch()
        return tid

    def cancel(self, tid):
        t = self.tasks.get(tid)
        if t is None:
            return
        h = t["helper"]
        if h is None:                      # not started: just drop it
            self.tasks.pop(tid, None)
            self._call(t, "cancelled", None)
        else:
            h.cancel.value = 1

    def running(self, tid):
        return tid in self.tasks

    def busy(self):
        return bool(self.tasks)

    # ---- the window's timer calls this (30 times a second while anything is going on)
    def poll(self, budget=0.02):
        self.ui_active.value = 1 if time.monotonic() - self.last_touch < UI_QUIET else 0
        end = time.monotonic() + budget
        while time.monotonic() < end:
            try:
                tid, kind, data = self.result_q.get_nowait()
            except _queue.Empty:
                break
            except (EOFError, OSError):
                break
            if kind == "ready":
                r, slot, pid = data
                for h in self.helpers:
                    if h.role == r and h.slot == slot:
                        h.pid, h.ready = pid, True
                continue
            t = self.tasks.get(tid)
            if t is None:
                continue
            if kind in ("ok", "failed"):
                self.tasks.pop(tid, None)
                if t["helper"] is not None:
                    t["helper"].tid = None
                if kind == "ok":
                    try:
                        data = pickle.loads(data)
                    except Exception:
                        kind, data = "failed", traceback.format_exc()
            self._call(t, kind, data)
        self._check_helpers()
        self._dispatch()

    def _call(self, t, kind, data):
        cb = t.get("cb")
        if cb is not None:
            try:
                cb(kind, data)
            except Exception:
                self.log(traceback.format_exc())

    def _check_helpers(self):
        for h in list(self.helpers):
            if h.proc.is_alive():
                continue
            self.helpers.remove(h)
            if self.xowner.value and self.xowner.value == h.pid:
                self.xowner.value = 0      # it stopped while writing: free the write lock
                try:
                    self.xlock.release()
                except ValueError:
                    pass
            what = ""
            if h.tid is not None:
                t = self.tasks.pop(h.tid, None)
                if t is not None:
                    func = t["msg"][0]
                    what = f" while doing {func}"
                    if h.role == "gpu" and h.proc.exitcode not in (0, None):
                        n = self._note_crash(func)
                        what += (" - next time it runs on the graphics card without FP16" if n < GPU_CRASHES_MAX
                                 else f" - from now on that job runs on the CPU (for {GPU_CRASH_DAYS} days)")
                    self._call(t, "failed", f"the {h.role} helper stopped unexpectedly (exit code {h.proc.exitcode})")
            self.log(f"helper {h.role}{h.slot} ended (exit code {h.proc.exitcode}){what}")

    def _dispatch(self):
        if self.closed:
            return
        skipped = []
        while self.waiting:
            pri, _seq, tid = heapq.heappop(self.waiting)
            t = self.tasks.get(tid)
            if t is None:
                continue
            func, args, kw, pri, role_, data_dir = t["msg"]
            h = self._idle_helper(role_)
            if h is None:
                skipped.append((pri, _seq, tid))
                continue
            h.tid, t["helper"] = tid, h
            h.cancel.value = 0
            opts = dict(t.get("opts") or {})
            import cryptbox
            if cryptbox.unlocked():
                opts["mk"] = cryptbox.master()       # (1.55) an encrypted library: the helper needs its key
            h.task_q.put((tid, func, args, kw, pri, data_dir, opts))
        for item in skipped:
            heapq.heappush(self.waiting, item)
        # something you're waiting for (a scan, say) and its helper is busy with housekeeping: the
        # housekeeping job is asked to stop (it carries on another time) so yours starts now
        for pri, _seq, tid in skipped:
            if pri != NOW:
                continue
            role_ = self.tasks[tid]["msg"][4]
            mine = [h for h in self.helpers if h.role == role_]
            limit = 1 if role_ == "gpu" else self.n_cpu
            if len(mine) < limit or any(h.tid is not None and self.tasks.get(h.tid, {}).get("preempted")
                                        for h in mine):
                continue
            busy = [h for h in mine if h.tid is not None and self.tasks.get(h.tid) and
                    self.tasks[h.tid]["msg"][3] > NOW]
            if busy:
                victim = max(busy, key=lambda h: self.tasks[h.tid]["msg"][3])
                self.tasks[victim.tid]["preempted"] = True
                victim.cancel.value = 1

    def _idle_helper(self, role_):
        mine = [h for h in self.helpers if h.role == role_]
        for h in mine:
            if h.tid is None:
                return h
        limit = 1 if role_ == "gpu" else self.n_cpu
        if len(mine) < limit:
            return self._start(role_, len(mine))
        return None

    def _start(self, role_, slot):
        threads = 4 if role_ == "gpu" else 2
        task_q = self.ctx.Queue()
        cancel = self.ctx.Value("b", 0)
        proc = self.ctx.Process(target=helper_main, name=f"FaceSort helper {role_}{slot}", daemon=True,
                                args=(role_, slot, task_q, self.result_q, cancel, self.ui_active,
                                      self.xlock, self.xui, self.xowner, threads))
        # run from source: the helper starts from this small module, not the whole window
        # (the packaged exe never re-runs its main script in a helper anyway)
        main = sys.modules.get("__main__")
        old = getattr(main, "__file__", None)
        swap = old is not None and not getattr(sys, "frozen", False)
        if swap:
            main.__file__ = os.path.abspath(__file__)
        try:
            proc.start()
        finally:
            if swap:
                main.__file__ = old
        h = _Helper(role_, slot, proc, task_q, cancel)
        self.helpers.append(h)
        return h

    def _end(self, h, timeout=0.0):
        try:
            h.task_q.put(None)
        except Exception:
            pass
        if timeout:
            h.proc.join(timeout)
        if h in self.helpers and not h.proc.is_alive():
            self.helpers.remove(h)

    def shutdown(self, timeout=5.0):
        """FaceSort is closing: running jobs are asked to stop, then every helper ends."""
        self.closed = True
        for h in self.helpers:
            h.cancel.value = 1
            try:
                h.task_q.put(None)
            except Exception:
                pass
        end = time.monotonic() + timeout
        for h in self.helpers:
            h.proc.join(max(0.0, end - time.monotonic()))
        for h in self.helpers:
            if h.proc.is_alive():
                h.proc.terminate()
                h.proc.join(1)
        if self.xowner.value:
            self.xowner.value = 0
            try:
                self.xlock.release()
            except ValueError:
                pass
        self.helpers = []
        tasks, self.tasks = list(self.tasks.values()), {}
        for t in tasks:                    # (whoever waits for them hears they're over)
            self._call(t, "cancelled", None)
        self.waiting = []


_POOL = [None]


def pool():
    if _POOL[0] is None or _POOL[0].closed:
        _POOL[0] = Pool()
    return _POOL[0]


def has_pool():
    return _POOL[0] is not None and not _POOL[0].closed


if __name__ == "__main__":       # (a helper started from source runs this module as its main - nothing to do)
    pass
