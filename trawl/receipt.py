"""Receipts: a small file that says what a run printed, and lets anyone check it.

`trawl fast prog.trawl --receipt run.receipt` (or `trawl run ... --receipt`) writes a JSON
receipt next to the run: the program and every library it used (the full text, so the
receipt stands on its own), the Trawl version, how it was run, and the SHA-256 of exactly
what it printed.

`trawl verify run.receipt` rebuilds the program from the receipt and runs it again, by
default on the *other* engine (a native run is re-checked by the reference interpreter),
then compares the output byte for byte.

A long sweep is too slow to re-run on the interpreter. With the run's save file,
`trawl verify run.receipt --save run.sav --sample 8` re-runs 8 random blocks of every
sweep on the interpreter and checks them against the saved blocks; every other block is
replayed from the save file, and the whole output must still match the receipt.
"""
import datetime
import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time

from . import __version__
from .lang import TrawlError, parse, read_source

SWEEP_BLOCKS = 256  # the native runtime's block count (trawl_rt.h)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def normal(data):
    """Output bytes as a receipt counts them: Windows line ends read as plain newlines."""
    return data.replace(b"\r\n", b"\n")


def program_files(path):
    """The program at path and every library it uses: (Program, {name: text}, main name)."""
    src = read_source(path)
    used = []
    prog = parse(src, path, used=used)
    main = os.path.basename(path)
    files = {main: src}
    for u in used:
        name = os.path.basename(u)
        text = read_source(u)
        if name in files and files[name] != text:
            raise TrawlError(f"two different libraries are both called {name}; a receipt needs their names to differ")
        files[name] = text
    return prog, files, main


def files_digest(files):
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + b"\0" + files[name].encode() + b"\0")
    return h.hexdigest()


def compiler_name():
    try:
        from .cgen import find_cc
        cc = find_cc()
        r = subprocess.run([cc, "--version"], capture_output=True, text=True, timeout=30)
        return (r.stdout or r.stderr).splitlines()[0].strip()
    except (TrawlError, OSError, IndexError, subprocess.SubprocessError):
        return None


def write_receipt(path, *, files, main, engine, output, seconds, jobs=None, save=None):
    out = normal(output)
    text = out.decode("utf-8", "replace")
    receipt = {
        "receipt": 1,
        "trawl": __version__,
        "made": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine": engine,
        "jobs": jobs,
        "seconds": round(seconds, 3),
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "compiler": compiler_name() if engine == "fast" else None,
        "program": {"main": main, "sha256": files_digest(files), "files": files},
        "output": {"sha256": sha256(out), "bytes": len(out), "lines": text.count("\n"),
                   "head": text[:300], "tail": text[-300:] if len(text) > 300 else ""},
    }
    if save:
        with open(save, "rb") as fh:
            receipt["save"] = {"file": os.path.basename(save), "sha256": sha256(fh.read())}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    return receipt


class Tee:
    """Stands in for sys.stdout: passes everything through and keeps a copy."""

    def __init__(self, real):
        self.real, self.kept = real, []

    def write(self, s):
        self.kept.append(s)
        return self.real.write(s)

    def flush(self):
        self.real.flush()

    def isatty(self):
        return self.real.isatty()

    def data(self):
        return "".join(self.kept).encode("utf-8")


def run_native_teed(cmd, ticker=None, keep=True):
    """Run a native program, passing its output through as it comes; returns (exit code, bytes).

    With a progress Ticker, the output goes through it so its "still running" line is wiped first."""
    sys.stdout.flush()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    kept = []
    out = getattr(sys.stdout, "buffer", None)
    while True:
        chunk = p.stdout.read1(65536) if hasattr(p.stdout, "read1") else p.stdout.read(65536)
        if not chunk:
            break
        if keep:
            kept.append(chunk)
        if ticker is not None:
            ticker.write_bytes(chunk)
        elif out is not None:
            out.write(chunk)
            out.flush()
        else:
            sys.stdout.write(chunk.decode("utf-8", "replace"))
    return p.wait(), b"".join(kept)


# ---------------------------------------------------------------- save files (trawl_rt.h's format)

def save_check(data):
    """FNV-1a, the same check the native runtime writes after every saved block."""
    h = 14695981039346656037
    for byte in data:
        h = ((h ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def read_save(path):
    """The header stamp and the finished blocks of a native save file, keyed by (sweep, lo, hi, a, b)."""
    with open(path, "rb") as fh:
        data = fh.read()
    nl = data.find(b"\n")
    head = data[:nl].decode("ascii", "replace").split() if nl >= 0 else []
    if len(head) != 3 or head[:2] != ["trawl-save", "2"]:
        raise TrawlError(f"{path} isn't a Trawl save file this Trawl can read")
    stamp, pos, recs = head[2], nl + 1, {}
    while True:
        end = data.find(b"\n", pos)
        if end < 0:
            break
        line = data[pos:end]
        parts = line.split(b" ")
        if len(parts) != 9 or parts[0] != b"B":
            break
        try:
            no, lo, hi, a, b, count, length = (int(x) for x in parts[1:8])
            check = int(parts[8], 16)
        except ValueError:
            break
        body = end + 1
        if length < 0 or body + length + 1 > len(data) or data[body + length:body + length + 1] != b"\n":
            break
        if save_check(line[:line.rindex(b" ")] + data[body:body + length]) != check:
            break  # a damaged block: like the native run, trust nothing from here on
        recs[(no, lo, hi, a, b)] = (count, data[body:body + length].decode("utf-8"))
        pos = body + length + 1
    return stamp, recs


class Plan:
    """Replays a sweep's saved blocks, re-running a random few of them on the interpreter."""

    def __init__(self, recs, sample, seed):
        self.recs, self.sample, self.rng = recs, sample, random.Random(seed)
        self.no, self.checked, self.bad, self.missing, self.replayed = 0, 0, [], 0, 0

    def sweep(self, sea, block, lo, hi):
        no = self.no
        self.no += 1
        span = hi - lo
        nb = min(span, SWEEP_BLOCKS)
        pick = set(self.rng.sample(range(nb), min(self.sample, nb)))
        count = 0
        for k in range(nb):
            a, b = lo + span * k // nb, lo + span * (k + 1) // nb
            rec = self.recs.get((no, lo, hi, a, b))
            if rec is not None and k not in pick:
                sea.say(rec[1])
                count += rec[0]
                self.replayed += 1
                continue
            said, c = sea.capture(lambda: block(a, b))
            if rec is None:
                self.missing += 1
            else:
                self.checked += 1
                if (c, said) != rec:
                    self.bad.append((no, a, b, rec, (c, said)))
            sea.say(said)
            count += c
        return count


# ---------------------------------------------------------------- trawl verify

def first_difference(want, got):
    a, b = want.decode("utf-8", "replace"), got.decode("utf-8", "replace")
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
    line = a.count("\n", 0, i) + 1
    return f"they part at line {line}: the receipt has {a[i:i + 40]!r}, this run printed {b[i:i + 40]!r}"


def first_took(r):
    from .progress import clock
    s = r.get("seconds")
    return f" (the first run, with trawl {r['engine']}, took {clock(s)})" if isinstance(s, (int, float)) and s >= 1 else ""


def read_receipt(path):
    """Load a receipt and check its shape, so a damaged or hostile one fails with a plain message."""
    bad = f"{os.path.basename(path)} isn't a Trawl receipt"
    try:
        with open(path, encoding="utf-8-sig") as fh:
            r = json.load(fh)
    except (ValueError, UnicodeDecodeError):
        raise TrawlError(f"{bad} (it isn't readable JSON)") from None
    except OSError as e:
        raise TrawlError(f"can't open {os.path.basename(path)}: {e.strerror or e}") from None
    if not isinstance(r, dict) or type(r.get("receipt")) is not int:
        raise TrawlError(bad)
    if r["receipt"] != 1:
        raise TrawlError(f"{bad} this Trawl can read (it's from a newer Trawl)")
    prog, out = r.get("program"), r.get("output")
    if not (isinstance(prog, dict) and isinstance(prog.get("files"), dict) and isinstance(prog.get("main"), str)
            and isinstance(prog.get("sha256"), str) and prog["main"] in prog["files"]
            and all(isinstance(k, str) and isinstance(v, str) for k, v in prog["files"].items())
            and isinstance(out, dict) and isinstance(out.get("sha256"), str)
            and isinstance(out.get("bytes"), int) and isinstance(out.get("head"), str)
            and r.get("engine") in ("run", "fast")
            and (r.get("save") is None or (isinstance(r["save"], dict) and isinstance(r["save"].get("sha256"), str)))):
        raise TrawlError(f"{bad} (parts of it are missing or the wrong kind)")
    for name in prog["files"]:
        # a file name, nothing more: a receipt must never write outside its own temporary folder
        if name in ("", ".", "..") or "/" in name or "\\" in name or ":" in name or "\0" in name:
            raise TrawlError(f"{bad}: it names a file `{name}` outside its own folder")
    for key in ("trawl", "made", "machine"):
        r[key] = str(r.get(key, "?"))
    return r


def verify(path, engine=None, save=None, sample=0, max_steps=50_000_000, seed=None, say=print, ticker=None):
    """Check a receipt. Returns True when it holds.

    The re-run itself is silent, so it happens inside a progress Ticker ("still checking... 1:12")."""
    from .progress import Ticker
    ticker = ticker or Ticker("still checking")
    r = read_receipt(path)
    files, main = r["program"]["files"], r["program"]["main"]
    want = r["output"]["sha256"].lower()
    made = f"trawl {r['engine']}" + (f" --jobs {r['jobs']}" if r.get("jobs") else "")
    say(f"receipt: {main}, made {r['made']} with {made} on Trawl {r['trawl']} ({r['machine']})")
    if r["trawl"] != __version__:
        say(f"  note: this receipt was made on Trawl {r['trawl']}; this is Trawl {__version__}")
    if files_digest(files) != r["program"]["sha256"]:
        say("✗ the program inside the receipt was changed after it was made")
        return False
    if save is not None:
        engine = "run"
        if sample < 1:
            raise TrawlError("--save needs --sample N: how many blocks of each sweep to re-run")
    elif sample or seed is not None:
        raise TrawlError("--sample and --seed only work with --save FILE. without a save file, "
                         "verify re-runs the whole program")
    if engine is None:
        engine = "run" if r["engine"] == "fast" else "fast"
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in files.items():
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        main_path = os.path.join(tmp, main)
        used = []
        prog = parse(files[main], main_path, flat=True, used=used)
        for u in used:  # every library must come from the receipt, never from this machine
            if os.path.normcase(os.path.dirname(os.path.realpath(u))) != os.path.normcase(os.path.realpath(tmp)):
                say(f"✗ the receipt leaves out the library {os.path.basename(u)}, so it can't vouch for the program")
                return False
        t0 = time.time()
        plan = None
        if engine == "run":
            from .interp import run, uses_worlds
            if save is not None:
                if uses_worlds(prog):
                    raise TrawlError("this program uses worlds, so its sweeps were never saved in blocks")
                if not r.get("save"):
                    say("✗ this receipt was made without a save file, so it can't vouch for one")
                    return False
                try:
                    with open(save, "rb") as fh:
                        data = fh.read()
                except OSError as e:
                    raise TrawlError(f"can't open {os.path.basename(save)}: {e.strerror or e}") from None
                if sha256(data) != r["save"]["sha256"].lower():
                    say(f"✗ {os.path.basename(save)} is not the save file this receipt was made with")
                    return False
                from .cgen import to_c
                stamp, recs = read_save(save)
                code = to_c(prog, main_path)
                if f'#define TRAWL_PROG_HASH "{stamp}"' not in code:
                    say(f"✗ {os.path.basename(save)} was written by a different program")
                    return False
                seed = random.randrange(1 << 30) if seed is None else seed
                plan = Plan(recs, sample, seed)
            say(f"re-running it on the reference interpreter{first_took(r)}")
            with ticker:
                out, sunk = run(prog, max_steps=max_steps, plan=plan)
            got = (out + "\n" if sunk is None else out + "\n" + f"({sunk} worlds sank along the way)\n").encode("utf-8")
            how = "the reference interpreter"
        else:
            from .cgen import build
            exe = os.path.join(tmp, "prog" + (".exe" if os.name == "nt" else ""))
            build(prog, exe, main_path)
            say(f"re-running it on the native compiler{first_took(r)}")
            with ticker:
                p = subprocess.run([exe], capture_output=True)
            if p.returncode != 0:
                say(f"✗ the native run stopped with an error: {p.stderr.decode('utf-8', 'replace').strip()}")
                return False
            got = p.stdout
            how = "the native compiler"
    got = normal(got)
    took = time.time() - t0
    ok = True
    if plan is not None:
        say(f"sweeps: re-ran {plan.checked} saved block{'s' if plan.checked != 1 else ''} on the interpreter "
            f"(seed {seed}), replayed {plan.replayed} from {os.path.basename(save)}"
            + (f", ran {plan.missing} the save file didn't have" if plan.missing else ""))
        for no, a, b, (wc, wout), (gc, gout) in plan.bad[:5]:
            say(f"✗ sweep {no + 1}, n from {a} to {b - 1}: saved count {wc}, re-run count {gc}"
                + ("" if wout == gout else f"; saved words {wout[:60]!r}, re-run words {gout[:60]!r}"))
        ok = not plan.bad
    lines = got.count(b"\n")
    if plan is not None:
        how = "the interpreter, with the rest replayed from the save file"
    if sha256(got) == want and (r["output"]["bytes"] != len(got)
                                or got.decode("utf-8", "replace")[:300] != r["output"]["head"]):
        say(f"✗ re-ran on {how} in {took:.1f} s: the output matches, but the receipt's own byte count or "
            "first lines were changed after it was made")
        ok = False
    elif sha256(got) == want:
        spot = (f", SPOT CHECK: {plan.checked} of {plan.checked + plan.replayed} blocks re-run"
                if plan is not None else "")
        say(f"{'✓' if ok else '✗'} re-ran on {how} in {took:.1f} s: same output{spot}, "
            f"{len(got)} bytes, {lines} lines (sha256 {want[:16]})")
        if ok and plan is not None:
            say(f"  this was a spot check: {plan.checked} of {plan.checked + plan.replayed} saved blocks were "
                "re-run, the rest are trusted from the save file. --sample with a bigger number checks more")
    else:
        size = (f"{len(got)} bytes, the receipt says {r['output']['bytes']}" if len(got) != r['output']['bytes']
                else f"the same size, {len(got)} bytes, but different bytes")
        say(f"✗ re-ran on {how} in {took:.1f} s: the output is different ({size})")
        head = r["output"]["head"].encode("utf-8")
        if got[:len(head)] != head:
            say("  " + first_difference(head, got[:len(head)]))
        ok = False
    return ok
