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
from .lang import TrawlError, parse

SWEEP_BLOCKS = 256  # the native runtime's block count (trawl_rt.h)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def normal(data):
    """Output bytes as a receipt counts them: Windows line ends read as plain newlines."""
    return data.replace(b"\r\n", b"\n")


def program_files(path):
    """The program at path and every library it uses: (Program, {name: text}, main name)."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    used = []
    prog = parse(src, path, used=used)
    main = os.path.basename(path)
    files = {main: src}
    for u in used:
        name = os.path.basename(u)
        with open(u, encoding="utf-8") as fh:
            text = fh.read()
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

    def data(self):
        return "".join(self.kept).encode("utf-8")


def run_native_teed(cmd):
    """Run a native program, passing its output through as it comes; returns (exit code, bytes)."""
    sys.stdout.flush()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    kept = []
    out = getattr(sys.stdout, "buffer", None)
    while True:
        chunk = p.stdout.read1(65536) if hasattr(p.stdout, "read1") else p.stdout.read(65536)
        if not chunk:
            break
        kept.append(chunk)
        if out is not None:
            out.write(chunk)
            out.flush()
        else:
            sys.stdout.write(chunk.decode("utf-8", "replace"))
    return p.wait(), b"".join(kept)


# ---------------------------------------------------------------- save files (trawl_rt.h's format)

def read_save(path):
    """The header stamp and the finished blocks of a native save file, keyed by (sweep, lo, hi, a, b)."""
    with open(path, "rb") as fh:
        data = fh.read()
    nl = data.find(b"\n")
    head = data[:nl].decode("ascii", "replace").split() if nl >= 0 else []
    if len(head) != 3 or head[:2] != ["trawl-save", "1"]:
        raise TrawlError(f"{path} isn't a Trawl save file")
    stamp, pos, recs = head[2], nl + 1, {}
    while True:
        end = data.find(b"\n", pos)
        if end < 0:
            break
        parts = data[pos:end].split()
        if len(parts) != 8 or parts[0] != b"B":
            break
        no, lo, hi, a, b, count, length = (int(x) for x in parts[1:])
        body = end + 1
        if body + length + 1 > len(data) or data[body + length:body + length + 1] != b"\n":
            break
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


def verify(path, engine=None, save=None, sample=0, max_steps=50_000_000, seed=None, say=print):
    """Check a receipt. Returns True when it holds."""
    with open(path, encoding="utf-8") as fh:
        r = json.load(fh)
    if r.get("receipt") != 1:
        raise TrawlError(f"{path} isn't a Trawl receipt (or it's from a newer Trawl)")
    files, main = r["program"]["files"], r["program"]["main"]
    want = r["output"]["sha256"]
    made = f"trawl {r['engine']}" + (f" --jobs {r['jobs']}" if r.get("jobs") else "")
    say(f"receipt: {main}, made {r['made']} with {made} on Trawl {r['trawl']} ({r['machine']})")
    if files_digest(files) != r["program"]["sha256"]:
        say("✗ the program inside the receipt was changed after it was made")
        return False
    if save is not None:
        engine = "run"
        if sample < 1:
            raise TrawlError("--save needs --sample N: how many blocks of each sweep to re-run")
    if engine is None:
        engine = "run" if r["engine"] == "fast" else "fast"
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in files.items():
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        main_path = os.path.join(tmp, main)
        prog = parse(files[main], main_path)
        t0 = time.time()
        plan = None
        if engine == "run":
            from .interp import run, uses_worlds
            if save is not None:
                if uses_worlds(prog):
                    raise TrawlError("this program uses worlds, so its sweeps were never saved in blocks")
                if r.get("save"):
                    with open(save, "rb") as fh:
                        if sha256(fh.read()) != r["save"]["sha256"]:
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
            out, sunk = run(prog, max_steps=max_steps, plan=plan)
            got = (out + "\n" if sunk is None else out + "\n" + f"({sunk} worlds sank along the way)\n").encode("utf-8")
            how = "the reference interpreter"
        else:
            from .cgen import build
            exe = os.path.join(tmp, "prog" + (".exe" if os.name == "nt" else ""))
            build(prog, exe, main_path)
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
    if sha256(got) == want:
        say(f"{'✓' if ok else '✗'} re-ran on {how} in {took:.1f} s: same output, "
            f"{len(got)} bytes, {lines} lines (sha256 {want[:16]})")
    else:
        say(f"✗ re-ran on {how} in {took:.1f} s: the output is different "
            f"({len(got)} bytes, the receipt says {r['output']['bytes']})")
        head = r["output"]["head"].encode("utf-8")
        if got[:len(head)] != head:
            say("  " + first_difference(head, got[:len(head)]))
        ok = False
    return ok
