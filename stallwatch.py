"""Speed report: notices whenever the window stops responding for a moment (a "stall") and
writes down which part of FaceSort was busy, so slowness on a real library can be tracked down.

A timer on the window ticks every 100 ms. A small helper thread checks the tick; while it's
late it looks at what the window's thread is running (every 40 ms) and, when the window is
back, writes one line per stall to speed.log in the data folder:

    2026-11-12 03:20:11  stall 840 ms  view=person  [rebuild_tree 620 ms  < refresh_all  ...]

Nothing leaves your PC; the file is only for sending along when something feels slow.
"""
import collections
import os
import sys
import threading
import time

MIN_STALL = 0.25        # seconds without a tick before it counts
SAMPLE = 0.04
KEEP_LINES = 2000       # speed.log is trimmed to about this many lines
OURS = os.path.dirname(os.path.abspath(__file__))


def _frames(frame):
    """FaceSort's own functions on the stack, innermost first: ["rebuild_tree (mw_lists.py:212)", ...]"""
    out = []
    while frame is not None:
        co = frame.f_code
        fn = co.co_filename
        if os.path.dirname(fn) in ("", OURS):        # (frozen builds name our files without a folder)
            out.append(f"{co.co_name} ({os.path.basename(fn)}:{frame.f_lineno})")
        frame = frame.f_back
    return out


class StallWatch:
    def __init__(self, path, describe=lambda: ""):
        self.path = path
        self.describe = describe              # e.g. which view is open, called from the window's thread
        self.beat = time.monotonic()
        self.main_id = threading.main_thread().ident
        self.recent = collections.deque(maxlen=50)
        self._ctx = ""
        self._stop = False
        self._lines = 0
        self.thread = threading.Thread(target=self._run, name="stallwatch", daemon=True)

    def start(self, parent):
        from PySide6.QtCore import QTimer
        self.timer = QTimer(parent)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.thread.start()

    def tick(self):
        self.beat = time.monotonic()
        try:
            self._ctx = self.describe()
        except Exception:
            self._ctx = ""

    def stop(self):
        self._stop = True

    def _run(self):
        while not self._stop:
            time.sleep(SAMPLE)
            if time.monotonic() - self.beat < MIN_STALL:
                continue
            start = self.beat
            counts = collections.Counter()
            first = {}
            samples = 0
            while time.monotonic() - self.beat >= 0.12 and self.beat == start and not self._stop:
                fr = sys._current_frames().get(self.main_id)
                stack = _frames(fr) if fr is not None else []
                del fr
                samples += 1
                for depth, name in enumerate(dict.fromkeys(stack)):     # each function once per sample
                    counts[name] += 1
                    first.setdefault(name, depth)
                time.sleep(SAMPLE)
            if self._stop:
                return
            took = time.monotonic() - start
            if took < MIN_STALL or not samples:
                continue
            # the innermost functions that were busy for a good part of the stall
            busy = [n for n, k in counts.items() if k >= max(1, samples * 0.25)]
            busy.sort(key=lambda n: first[n])
            parts = [f"{n} {int(1000 * took * counts[n] / samples)} ms" for n in busy[:6]]
            line = (time.strftime("%Y-%m-%d %H:%M:%S") + f"  stall {int(took * 1000)} ms  {self._ctx}  ["
                    + "  <  ".join(parts) + "]")
            self.recent.append(line)
            self._write(line)

    def _write(self, line):
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._lines += 1
            if self._lines % 200 == 0:
                with open(self.path, encoding="utf-8") as f:
                    lines = f.readlines()
                if len(lines) > KEEP_LINES:
                    with open(self.path, "w", encoding="utf-8") as f:
                        f.writelines(lines[-KEEP_LINES:])
        except Exception:
            pass
