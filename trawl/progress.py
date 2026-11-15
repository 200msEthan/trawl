"""A "still running" line for long runs, so a quiet hunt never looks frozen.

While a run is quiet for more than a second, the terminal shows

    | still running... 1:12  (Ctrl+C stops it)

on stderr, redrawn in place and wiped before the run prints anything else. It only
appears when stderr is a terminal (TRAWL_PROGRESS=1 forces it on, TRAWL_PROGRESS=0 off),
and it never goes to stdout, so piped output and receipts stay exactly what the program
printed. Plain ASCII, because an old Windows console can't show every character.
"""
import os
import sys
import threading
import time

SPIN = "|/-\\"
QUIET = 1.0  # seconds of silence before the line appears


def wanted(stream=None):
    force = os.environ.get("TRAWL_PROGRESS", "")
    if force in ("0", "1"):
        return force == "1"
    stream = stream or sys.stderr
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def clock(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class Ticker:
    """Draws the line from a background thread. Use as a context manager around the quiet work."""

    def __init__(self, label="still running", err=None, out=None, enabled=None):
        self.label = label
        self.err = err or sys.stderr
        self.out = out or sys.stdout
        self.enabled = wanted(self.err) if enabled is None else enabled
        try:
            self.shared = self.out.isatty() or os.environ.get("TRAWL_PROGRESS") == "1"
        except (AttributeError, ValueError):
            self.shared = False
        self.lock = threading.Lock()
        self.drawn = 0             # length of the line on screen now
        self.line_start = True     # did the program's own output last end a line?
        self.last_out = 0.0
        self.stop = threading.Event()
        self.t0 = time.monotonic()
        self.thread = None

    # -- the program's output passes through here, so the line is wiped first
    def write(self, s):
        if not s:
            return 0
        with self.lock:
            self._wipe()
            n = self.out.write(s)
            self.line_start = s.endswith("\n")
            self.last_out = time.monotonic()
        return n

    def write_bytes(self, b):
        with self.lock:
            self._wipe()
            buf = getattr(self.out, "buffer", None)
            if buf is not None:
                buf.write(b)
                buf.flush()
            else:
                self.out.write(b.decode("utf-8", "replace"))
                self.out.flush()
            self.line_start = b.endswith(b"\n")
            self.last_out = time.monotonic()

    def flush(self):
        self.out.flush()

    def __getattr__(self, name):  # anything else (encoding, isatty...) is the real stdout's
        return getattr(self.out, name)

    # -- drawing
    def _wipe(self):
        if self.drawn:
            self.err.write("\r" + " " * self.drawn + "\r")
            self.err.flush()
            self.drawn = 0

    def _draw(self, k):
        now = time.monotonic()
        if now - self.t0 < QUIET or (self.shared and now - self.last_out < QUIET):
            return
        self.out.flush()
        if self.shared and not self.line_start:
            self.err.write("\n")  # don't draw over a half-printed line
            self.line_start = True
        text = f"{SPIN[k % len(SPIN)]} {self.label}... {clock(now - self.t0)}  (Ctrl+C stops it)"
        pad = max(0, self.drawn - len(text))
        self.err.write("\r" + text + " " * pad)
        self.err.flush()
        self.drawn = len(text)

    def _loop(self):
        k = 0
        while not self.stop.wait(0.25):
            with self.lock:
                try:
                    self._draw(k)
                except (OSError, ValueError):
                    return
            k += 1

    def __enter__(self):
        if self.enabled:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *exc):
        if self.thread is not None:
            self.stop.set()
            self.thread.join()
            with self.lock:
                self._wipe()
        return False

    def seconds(self):
        return time.monotonic() - self.t0

    def done(self, what="done"):
        """The closing line on stderr (only when the ticker was on): '~ done in 2:05'."""
        if self.enabled:
            self.out.flush()
            if not self.line_start and self.shared:
                self.err.write("\n")
            self.err.write(f"~ {what} in {clock(self.seconds())}\n")
            self.err.flush()
