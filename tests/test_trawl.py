"""Trawl's test suite: python -m pytest

- the language, word by word, on both engines
- error messages, word for word, on both engines
- big numbers: hundreds of random operations checked against Python's own integers
- every old Tide program in tests/programs/ prints exactly what the original Tide engine
  printed (the .out files were recorded from it)
- every example gives the same output on both engines
"""
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys

import pytest

from trawl.cgen import build
from trawl.interp import run
from trawl.lang import TrawlError, parse

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)
HAVE_CC = bool(os.environ.get("CC") or any(shutil.which(c) for c in ("gcc", "clang", "cc")))
needs_cc = pytest.mark.skipif(not HAVE_CC, reason="no C compiler (install gcc or clang)")


def interp(src, file="<program>", **kw):
    out, sunk = run(parse(src, file), **kw)
    return out + "\n" if sunk is None else out + "\n" + f"({sunk} worlds sank along the way)\n"


class Native:
    """Build and run programs natively, caching executables by source."""

    def __init__(self, tmp):
        self.tmp, self.n = tmp, 0

    def __call__(self, src, file="<program>", expect_error=False):
        self.n += 1
        exe = os.path.join(self.tmp, f"p{self.n}" + (".exe" if os.name == "nt" else ""))
        build(parse(src, file), exe, file)
        r = subprocess.run([exe], capture_output=True, text=True, encoding="utf-8", timeout=600)
        if expect_error:
            assert r.returncode != 0, r.stdout
            return r.stderr
        assert r.returncode == 0, r.stderr
        return r.stdout


@pytest.fixture(scope="session")
def native(tmp_path_factory):
    return Native(str(tmp_path_factory.mktemp("native")))


ENGINES = ["interp", pytest.param("native", marks=needs_cc)]


@pytest.fixture(params=ENGINES)
def engine(request, native):
    if request.param == "interp":
        return interp
    return native


# ---------------------------------------------------------------- the language

@pytest.mark.parametrize("src,want", [
    ("2 3 + 4 * .", "20 "),
    ("5 [ dup . 1 - ]", "5 4 3 2 1 "),
    (": sq dup * ; 7 sq .", "49 "),
    (": fact 1 swap [ swap over * swap 1 - ] drop ; 5 fact .", "120 "),
    ("101 110 111 100 emit emit emit emit", "done"),
    ("1 2 3 fold .s", "<3 2 1> "),
    ("1 if 10 else 20 then . 0 if 10 else 20 then .", "10 20 "),
    ('." hello sea"', "hello sea"),
    ("1 2 3 rot .s", "<2 3 1> "),
    ("3 4 over .s", "<3 4 3> "),
    ("1 2 swap .s drop drop depth .", "<2 1> 0 "),
    ("-7 2 / . -7 2 % . 7 -2 / . 7 -2 % . -7 -2 / . -7 -2 % .", "-4 1 -4 -1 3 -1 "),
    ("3 5 < . 5 3 < . 4 4 = . 9 2 > .", "1 0 1 1 "),
    ("1234 10 digsum . 255 2 digsum . 0 10 digsum . -1234 10 digsum .", "10 8 0 10 "),
    ("1234 10 altsum . 121 10 altsum . 0 10 altsum . 5 2 altsum .", "2 0 0 2 "),
    ("3 4 power . 0 0 power . -2 3 power . 2 100 power .", "81 1 -8 1267650600228229401496703205376 "),
    ("42 7 bury 7 dig . 3 dig . .bed", "42 0 {7:42} "),
    ("5 1 bury 6 2 bury drain 1 dig . 2 dig . .bed", "0 0 {} "),
    ("5 -3 bury 0 -3 bury .bed 9 -100000 bury 8 123456789 bury .bed", "{} {-100000:9 123456789:8} "),
    ("1 [ 1 dig 1 + 1 bury 1 dig 5 = if 0 then ] drop 1 dig .", "5 "),
    ("0 200000 [ swap 1 + swap 1 - ] drop .", "200000 "),
    ("1500 [ dup dup bury 1 - ] drop 1499 dig .", "1499 "),
    ("12345678901234567890123 dup * .", "152415787532388367504942236884722755800955129 "),
    ("1 2 3 depth . .s", "3 <1 2 3> "),
    ("228 emit 8364 emit", "ä€"),
])
def test_words(engine, src, want):
    assert engine(src) == want + "\n"


@pytest.mark.parametrize("src,want", [
    ("use stdlib 10 fib . 25 fib .", "55 75025 "),
    ("use stdlib 10 fact . 30 fact .", "3628800 265252859812191058636308480000000 "),
    ("use stdlib 1071 462 gcd . -7 abs . 3 9 max . 3 9 min . 4 even . 7 even .", "21 7 9 3 1 0 "),
    ("use stdlib 97 prime . 91 prime . 2 prime . 1 prime .", "1 0 1 0 "),
    ("use stdlib 1 2 nip . 1 2 tuck .s", "2 <2 1 2> "),
])
def test_stdlib(engine, src, want):
    assert engine(src) == want + "\n"


def test_numbers_library(engine):
    src = ("use numbers 1000000 isqrt . 99 isqrt . 0 isqrt . 123456789012345678901234567890 isqrt . "
           "720 2 vp . 720 3 vp . 100 7 legendre . 6 6 7 carries . 19295451536 19295451536 2 carries . "
           "3 7 1000000007 powmod . 2 100 1000 powmod . 0 10 ndigits . 1000 10 ndigits . 1234 10 reverse . "
           "720 5 smooth . 721 5 smooth . 89 nextprime .")
    import math
    want = [math.isqrt(10**6), 9, 0, math.isqrt(123456789012345678901234567890), 4, 2, 16, 1,
            bin(19295451536).count("1"), pow(3, 7, 10**9 + 7), pow(2, 100, 1000), 1, 4, 4321, 1, 0, 97]
    assert engine(src) == " ".join(map(str, want)) + " \n"


def test_orbits_library(engine):
    src = ": step dup * 31 % ; use orbits 5 orbit . . 2 orbit . . 3 orbit . . 5 lands . 3 4 steps ."

    def orbit(x):
        seen, i = {}, 0
        while x not in seen:
            seen[x], x, i = i, x * x % 31, i + 1
        return seen[x], i - seen[x]

    t5, l5 = orbit(5); t2, l2 = orbit(2); t3, l3 = orbit(3)
    assert engine(src) == f"{l5} {t5} {l2} {t2} {l3} {t3} 5 28 \n"


def test_echo_finds_loop_lengths(engine):
    # a counter that wraps at 7, watched by echo: the 8th look repeats the 1st
    src = "1 [ drop 1 dig 1 + 7 % 1 bury 1 1 echo dup 0 = ] drop . hush 1 1 echo ."
    assert engine(src) == "7 0 \n"


def test_worlds(engine):
    src = "1 9 wave dup dup * 50 > want . more"  # every x from 8 to 9 ... kept
    assert engine(src) == "8 9 \n(9 worlds sank along the way)\n"
    src = "1 5 wave dup . 3 - dup * aim"  # keep the world whose x is closest to 3
    assert engine(src) == "3 \n(5 worlds sank along the way)\n"


def test_pearls(engine):
    src = ": f dup 2 < if else dup 1 - f swap 2 - f + then ; pearl f 90 f . .pearls"
    assert engine(src) == "2880067194370816120 (91 pearls) \n"


# ---------------------------------------------------------------- errors

@pytest.mark.parametrize("src,msg", [
    ("+", "the tide went out too far: `+` wanted 2 numbers, the shore was empty"),
    ("1 rot", "`rot` wanted 3 numbers"),
    ("dup", "`dup` wanted 1 number,"),
    ("7 0 /", "you tried to split 7 into zero pieces. the ocean refuses."),
    ("7 0 %", "you tried to split 7 into zero pieces"),
    ("5 1 digsum", "base 1 has no digits. try 2 or more."),
    ("5 0 altsum", "base 0 has no digits"),
    ("2 -1 power", "tide numbers are whole: 2 to the power -1 would be a fraction"),
    ("1 9 wave 0 want", "every world sank (9 of them). the sea has no answer."),
    ("-1 emit", "emit wants a character number"),
    ("1 spf", "spf needs a whole number of 2 or more, not 1"),
    ("-12 spf", "spf needs a whole number of 2 or more, not -12"),
    ("0 factorings", "factorings needs a whole number of 1 or more, not 0"),
    ("1 5 sweep nope", "nobody in the sea knows the word `nope` yet, so `sweep` can't run it."),
    (": two dup ; 1 5 sweep two", "`sweep two` wants `two` to turn each n into one answer, but at n = 1 it left 2 numbers in its place"),
    (": gone drop ; 3 5 sweep gone", "but at n = 3 it left 0 numbers in its place"),
    (": ok 1 ; 5 sweep ok", "`sweep` wanted 2 numbers"),
    (": ok 1 ; 0 2 62 power sweep ok", "`sweep` runs from 0 up to 4611686018427387904, but both ends must lie between -2^62 and 2^62 - 1"),
])
def test_errors(engine, src, msg):
    if engine is interp:
        with pytest.raises(TrawlError, match=re.escape(msg)):
            interp(src)
    else:
        assert msg in engine(src, expect_error=True)


@pytest.mark.parametrize("src,msg", [
    ("1 flod", "did you mean `fold`"),
    (": dup 1 ;", "word of the sea itself"),
    ("1 [ 2", "was opened and the sea never closed it"),
    ("1 ]", "a stray `]`"),
    (": x 1", "never reached its `;`"),
    ("use nosuchlib", "no library called `nosuchlib`"),
    ("1 2 sweep", "`sweep` needs the name of one of your words after it"),
    ("1 2 sweep dup", "`sweep` needs the name of one of your words after it"),
])
def test_errors_found_before_running(src, msg):
    with pytest.raises(TrawlError, match=msg):
        prog = parse(src)
        if HAVE_CC:
            from trawl.cgen import to_c
            to_c(prog)
        else:
            run(prog)


@pytest.mark.parametrize("src", ["1 [ ]", "3 [ 1 + 1 - ]", ": next dup * 31 % ; 5 [ next ]"])
def test_interpreter_proves_stuck_loops(src):
    with pytest.raises(TrawlError, match="the tide is stuck"):
        interp(src)


def test_step_limit():
    with pytest.raises(TrawlError, match="the tide never went out"):
        interp("1 [ dup 1 + swap drop ]", max_steps=10_000)


@needs_cc
def test_native_stops_a_runaway_stack(native):
    assert "the stack grew past" in native("1 [ 1 ]", expect_error=True)


# ---------------------------------------------------------------- big numbers

EDGE = [0, 1, -1, 2, -2, 3, 7, -7, 10, 2**31, 2**32 - 1, 2**61, 2**62 - 1, 2**62, -(2**62), -(2**62) - 1,
        2**63, -(2**63), 2**64 + 5, 10**30, -(10**30) - 7, 3**80, -(5**60)]


SWEEPS = [
    (": sq? dup * 1 + prime ; 1 100 sweep sq? .", "19 "),
    (": show dup 7 % 0 = if dup . 1 else 0 then swap drop ; 1 50 sweep show .", "7 14 21 28 35 42 49 7 "),
    (": any 1 ; 5 5 sweep any . 9 2 sweep any .", "0 0 "),
    (": neg? 0 < ; -10 10 sweep neg? .", "10 "),
    # each call sets the cells it reads; the stack below is left alone
    (": odd? 2 % 7 bury 7 dig ; 42 0 21 sweep odd? . .", "10 42 "),
    # a sweep inside a word, and a sweep inside the swept word
    (": d? over swap % 0 = ; : divs dup 1 + 1 swap sweep d? swap drop ; : perfect? divs 4 = ; 1 30 sweep perfect? .", "9 "),
    (": hunt 0 swap sweep perfect? ; : d? over swap % 0 = ; : divs dup 1 + 1 swap sweep d? swap drop ; : perfect? divs 2 = ; 100 hunt .", "25 "),
    # a pearl word keeps its pearls across the sweep
    (": slow dup * 3 % ; pearl slow 1 10 sweep slow . 1 10 sweep slow . .pearls", "6 6 (9 pearls) "),
]


@pytest.mark.parametrize("src,want", SWEEPS)
def test_sweep(engine, src, want):
    assert engine(src) == want + "\n"


SWEEP_PROG = """
# n with a digit sum of 7, printed as found; cell 5 is scratch, set before it is read
: seven? 5 bury 5 dig 10 digsum 7 = dup if 5 dig . then ;
." start " 10 emit
0 3000 sweep seven? ." count " . 10 emit
1 1 sweep seven? .
"""


@needs_cc
def test_sweep_on_many_cores_matches_one_core(tmp_path):
    exe = str(tmp_path / ("sw" + (".exe" if os.name == "nt" else "")))
    build(parse(SWEEP_PROG), exe)
    want = interp(SWEEP_PROG)
    assert want.count(" ") > 50
    for jobs in ("1", "3", "0", "64"):
        r = subprocess.run([exe, "--jobs", jobs], capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr
        assert r.stdout == want, jobs
    if os.name != "nt":
        # started with SIGCHLD ignored (as some CI runners do): finished workers must not vanish unseen
        import signal
        ignore = lambda: signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        r = subprocess.run([exe, "--jobs", "4"], capture_output=True, text=True, timeout=300, preexec_fn=ignore)
        assert r.stdout == want and r.stderr == ""


@needs_cc
def test_sweep_keeps_going_when_every_running_block_is_done(tmp_path):
    # block 0 is slow, the rest are instant: the workers ahead fill the window and finish, then block 0
    # finishes too, and the runner must start more workers instead of waiting for ones that don't exist
    src = ": w? dup 0 = if 30000000 [ 1 - ] drop then 2 % ; 0 256 sweep w? ."
    exe = str(tmp_path / ("sk" + (".exe" if os.name == "nt" else "")))
    build(parse(src), exe)
    for jobs in ("2", "3"):
        r = subprocess.run([exe, "--jobs", jobs], capture_output=True, text=True, timeout=300)
        assert (r.returncode, r.stdout) == (0, "128 \n"), r.stderr
        assert "lost track" not in r.stderr  # (Windows adds a one-line note that it runs on one core)


@needs_cc
def test_sweep_stops_cleanly_when_a_block_fails(tmp_path):
    src = ": f? dup 1500 = if 0 0 / then dup 250 % 0 = if dup . then drop 1 ; 0 3000 sweep f? ."
    exe = str(tmp_path / ("sf" + (".exe" if os.name == "nt" else "")))
    build(parse(src), exe)
    with pytest.raises(TrawlError, match="zero pieces"):
        interp(src)
    for jobs in ("1", "4"):
        r = subprocess.run([exe, "--jobs", jobs], capture_output=True, text=True, timeout=300)
        assert r.returncode == 1
        assert r.stdout == "0 250 500 750 1000 1250 ", jobs  # everything printed before the failing n, in order
        assert "you tried to split 0 into zero pieces" in r.stderr


@needs_cc
def test_sweep_resumes_from_a_save_file(tmp_path):
    exe = str(tmp_path / ("sr" + (".exe" if os.name == "nt" else "")))
    build(parse(SWEEP_PROG), exe)
    want = interp(SWEEP_PROG)
    save = tmp_path / "run.sav"

    def go(*extra):
        r = subprocess.run([exe, "--save", str(save), *extra], capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr
        return r

    assert go().stdout == want
    text = save.read_bytes()
    records = text.split(b"\nB ")
    assert len(records) == 1 + 256  # the header and 256 blocks (the empty second sweep has none)
    # a run stopped partway: keep the first 100 blocks, then a torn half-written one
    kept = b"\nB ".join(records[:101]) + b"\nB 0 0 3000 1"
    save.write_bytes(kept)
    r = go("--jobs", "4")
    assert r.stdout == want and "resuming" in r.stderr and "100 finished blocks" in r.stderr
    assert save.read_bytes() == text
    # finished blocks really are replayed from the file, not run again: change one block's saved words
    old = b"B 0 0 3000 0 11 1 2\n7 \n"  # the block n = 0..10: count 1, two bytes of words
    assert old in text
    save.write_bytes(text.replace(old, b"B 0 0 3000 0 11 1 6\nSEVEN \n"))
    assert "SEVEN " in go().stdout
    # a different program doesn't resume from it
    exe2 = str(tmp_path / ("sr2" + (".exe" if os.name == "nt" else "")))
    build(parse(SWEEP_PROG.replace("7 =", "8 =")), exe2)
    r = subprocess.run([exe2, "--save", str(save)], capture_output=True, text=True, timeout=300)
    assert "starts over" in r.stderr and r.stdout == interp(SWEEP_PROG.replace("7 =", "8 ="))


def small_spf(n):
    d = 2
    while d * d <= n:
        if n % d == 0:
            return d
        d += 1
    return n


def test_prime_and_spf(engine):
    # every small n against plain trial division, then big numbers with known answers:
    # Mersenne primes and composites, strong pseudoprimes to many bases, factors above the
    # trial-division range (Pollard rho), squares of primes, and numbers past 64 bits
    src = "-5 1 [ drop dup prime . 1 + dup 3000 < ] drop drop 10 emit 2 1 [ drop dup spf . 1 + dup 3000 < ] drop drop 10 emit"
    want = " ".join(str(int(n >= 2 and small_spf(n) == n)) for n in range(-5, 3000)) + " \n"
    want += " ".join(str(small_spf(n)) for n in range(2, 3000)) + " \n"
    big = [
        ("2 61 power 1 - prime", 1), ("2 89 power 1 - prime", 1), ("2 127 power 1 - prime", 1),
        ("2 67 power 1 - prime", 0), ("2 67 power 1 - spf", 193707721),
        ("3215031751 prime", 0), ("3825123056546413051 prime", 0), ("318665857834031151167461 prime", 0),
        ("1000000007 1000000009 * prime", 0), ("1000000007 1000000009 * spf", 1000000007),
        ("1000003 1000033 * 1000037 * spf", 1000003), ("1000003 dup * spf", 1000003),
        ("600851475143 spf", 71), ("999999999999999989 prime", 1), ("4611686018427387903 prime", 0),
        ("2 61 power 1 - 2 31 power 1 - * spf", 2**31 - 1), ("2 89 power 1 - 3 * spf", 3),
        ("2 89 power 1 - spf", 2**89 - 1), ("1009 2 61 power 1 - * spf", 1009),
        # between 2^62 and 2^127: the compiled 128-bit path
        ("735644585302412067163926431748992513 spf", 796487719),
        ("26531086772967102834702945502975055537 spf", 639676214759),
        ("26531086772967102834702945502975055537 prime", 0), ("2 127 power 1 - 2 * spf", 2),
        ("2 107 power 1 - prime", 1), ("2 107 power 1 - 2 19 power 1 - * spf", 2**19 - 1),
    ]
    src += "\n" + " ".join(f"{e} ." for e, _ in big)
    want += " ".join(str(v) for _, v in big) + " "
    assert engine(src) == want + "\n"


def test_old_programs_keep_their_own_prime(engine):
    # prime and spf became built-ins after Tide; a program that defines its own keeps it
    assert engine(": prime drop 42 ;\n7 prime . 9 spf .\n: spf 1 + ;\n9 spf .") == "42 3 10 \n"


@needs_cc
def test_big_division_by_small_numbers(native):
    # big % small then big / small reuses one pass; different divisors, signs and garbage
    # collection in between must not mix the answers up
    rng = random.Random(11)
    parts, want = [], []
    for _ in range(300):
        a = rng.getrandbits(rng.choice([300, 600, 2000])) * rng.choice([1, -1])
        d, e = rng.choice([3, 10, 10**9, 2**32 - 1, -7]), rng.choice([10**9, 13, 2**31])
        parts.append(f"{a} dup {d} % . dup {d} / . dup {e} / . {d} % .")
        want += [a % d, a // d, a // e, a % d]
        if rng.random() < 0.1:
            parts.append("2 20000 power 300 dup [ drop swap 3 * swap 1 - dup ] drop drop drop")  # a lot of garbage
    for base in (2, 3, 10, 36, 1000, 65537, 2**32 - 1):
        a = rng.getrandbits(3000)
        parts.append(f"{a} {base} digsum . {a} {base} altsum .")
        x, t, alt, sign = a, 0, 0, 1
        while x:
            x, r = divmod(x, base)
            t += r; alt += sign * r; sign = -sign
        want += [t, alt]
    src = "\n".join(parts)
    assert native(src) == " ".join(map(str, want)) + " \n"


def test_stdlib_prime_is_the_fast_builtin():
    # the old library word did trial division: about 30,000 steps for this one number
    assert interp("use stdlib 1000000007 1000000009 * prime . 1000000007 prime .", max_steps=2000) == "0 1 \n"


def random_ints(rng, n):
    out = list(EDGE)
    while len(out) < n:
        bits = rng.choice([3, 10, 40, 61, 62, 63, 64, 90, 200, 500])
        x = rng.getrandbits(bits)
        out.append(-x if rng.random() < 0.5 else x)
    return out


@needs_cc
def test_big_number_arithmetic_matches_python(native):
    rng = random.Random(727)
    xs = random_ints(rng, 120)
    parts, want = [], []
    for _ in range(900):
        a, b = rng.choice(xs), rng.choice(xs)
        op = rng.choice("+-*/%=<>")
        if op in "/%" and b == 0:
            b = 3
        parts.append(f"{a} {b} {op} .")
        want.append({"+": a + b, "-": a - b, "*": a * b, "/": a // b if b else 0, "%": a % b if b else 0,
                     "=": int(a == b), "<": int(a < b), ">": int(a > b)}[op])
    for _ in range(80):
        a, base = rng.choice(xs), rng.choice([2, 3, 7, 10, 16, 1000, 2**40, 2**70])
        parts.append(f"{a} {base} digsum . {a} {base} altsum .")
        x, s, alt, sign = abs(a), 0, 0, 1
        while x:
            x, r = divmod(x, base)
            s += r; alt += sign * r; sign = -sign
        want += [s, alt]
    for _ in range(40):
        a, k = rng.choice(xs[:40]), rng.randrange(0, 40)
        parts.append(f"{a} {k} power .")
        want.append(a ** k)
    src = "\n".join(parts)
    assert native(src) == " ".join(map(str, want)) + " \n"
    assert interp(src) == " ".join(map(str, want)) + " \n"


@needs_cc
@pytest.mark.slow
def test_big_numbers_on_the_seabed_survive_garbage_collection(native):
    # 3 million big numbers made and dropped; the ones kept on the seabed must stay right
    src = "1 3000000 [ swap 10 40 power + dup 1 bury swap 1 - ] drop drop 1 dig 10 40 power / ."
    assert native(src) == "3000000 \n"


# ---------------------------------------------------------------- old Tide programs

PROGRAMS = sorted(p for p in (HERE / "programs").glob("*.tide") if (HERE / "programs" / (p.stem + ".out")).exists())
# these run for longer than the interpreter's 50-million-step limit (they were made for the compiler)
LONG = {"tide2_bigscan", "tide2_climb_run", "tide2_deck_luck", "tide2_deck_still", "tide2_deckp_luck",
        "tide2_ferry_floor_sets", "tide2_ferry_luck", "tide2_ferry_sets", "tide2_ferry_twins", "tide2_lens",
        "tide2_lens_predict2", "tide2_reef2d_luck", "tide2_reef2d_polyp", "tide2_reef2d_table", "tide2_reefK_fire",
        "tide2_reefK_luck", "tide2_reefK_table", "tide2_reefnd_more", "tide2_reefnd_table", "tide2_ring29",
        "tide2_ring29_inside", "tide2_ring8", "tide2_rule1011", "tide2_rules", "tide2_sync", "tide2_syncscan",
        "tide2_tub_k", "tide6_lagtucker", "tide2_climb_luck", "tide2_gap_grow", "tide2_lens_predict",
        "tide2_oferry_b", "tide2_tub2d_luck", "tide2_tub2d_many"}


# the quick run checks a sample of the old programs; --all checks every one
SAMPLE = set(PROGRAMS[::12])


def sampled(progs):
    return [p if p in SAMPLE else pytest.param(p, marks=pytest.mark.slow) for p in progs]


@needs_cc
@pytest.mark.parametrize("prog", sampled(PROGRAMS), ids=lambda p: p.stem)
def test_old_tide_program_native(native, prog):
    want = (prog.parent / (prog.stem + ".out")).read_text(encoding="utf-8")
    assert native(prog.read_text(encoding="utf-8"), str(prog)) == want


@pytest.mark.parametrize("prog", sampled([p for p in PROGRAMS if p.stem not in LONG]), ids=lambda p: p.stem)
def test_old_tide_program_interpreter(prog):
    want = (prog.parent / (prog.stem + ".out")).read_text(encoding="utf-8")
    assert interp(prog.read_text(encoding="utf-8"), str(prog)) == want


# ---------------------------------------------------------------- examples

QUICK_EXAMPLES = ["hello", "whirlpools", "reef", "queens", "route", "pearls"]


@needs_cc
@pytest.mark.parametrize("name", QUICK_EXAMPLES)
def test_example_same_on_both_engines(native, name):
    path = ROOT / "examples" / f"{name}.trawl"
    src = path.read_text(encoding="utf-8")
    assert native(src, str(path)) == interp(src, str(path))


@needs_cc
@pytest.mark.slow
def test_a343507_reproduces_the_published_terms(native):
    path = ROOT / "examples" / "a343507.trawl"
    src = path.read_text(encoding="utf-8") + "\n2 0 1000 search . 3 0 10000 search . 4 0 10000 search . 5 0 1000000 search . 6 0 10000000 search ."
    assert native(src, str(path)) == "208 3475 8174 252965 3648835 \n"


ERDOS = ROOT / "examples" / "erdos727"


@needs_cc
@pytest.mark.slow
def test_erdos727_families_match_an_independent_check(native):
    # counts checked separately in Python with Legendre's formula
    k4 = (ERDOS / "quartic_k4.trawl")
    assert native("use quartic_k4\n2 300 hunt", str(k4)) == "69 126 168 188 199 count 5 \n"
    k3 = (ERDOS / "land_k3.trawl")
    assert native("use land_k3\n0 2000 hunt", str(k3)) == "count 288 \n"


def test_erdos727_k4_family_on_the_interpreter():
    k4 = ERDOS / "quartic_k4.trawl"
    assert interp("use quartic_k4\n60 80 hunt", str(k4)) == "69 count 1 \n"


def test_playground_page_is_up_to_date():
    # playground/index.html embeds the interpreter; rebuild it when the interpreter changes
    r = subprocess.run([sys.executable, str(ROOT / "playground" / "build.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_playground_196_curse_catches_and_now_at():
    # the playground shows every printed line that starts with a number as a catch, and "now at"
    # shows the number at the bottom of the stack while a hunt runs (playground/template.html)
    from trawl.interp import Sea
    src = (ROOT / "playground" / "examples" / "curse.trawl").read_text(encoding="utf-8")

    class Watch(Sea):
        bottoms = set()

        def exec(self, body):
            if self.stack:
                self.bottoms.add(self.stack[0])
            return Sea.exec(self, body)

    sea = Watch()
    lines = sea.run(parse(src)).splitlines()
    catches = [ln for ln in lines if re.match(r"^\s*(?:[A-Za-z]\w{0,3} = )?(-?\d+)", ln)]
    # the 13 known Lychrel candidates below 1000 (OEIS A023108) are the catches; summaries start with "= "
    assert [int(c.split()[0]) for c in catches] == [196, 295, 394, 493, 592, 689, 691, 788, 790, 879, 887, 978, 986]
    # copycats: only 196 and 879 start their own family below 1000 (Lychrel seeds, OEIS A063048)
    assert [c.split()[0] for c in catches if "new family" in c] == ["196", "879"]
    assert "978 is cursed, a copycat of 879 " in lines
    assert lines[-3] == "= 89 broke free after 24 flips: the toughest escape"
    assert lines[-2] == "= 2 cursed families, started by: 196 879 "
    assert lines[-1] == "Tested every number up to 1000 and found 13 cursed numbers: those families and their copycats"
    # "now at" must show the number being tested, never the huge number it grows into
    assert max(sea.bottoms) <= 1001


# ---------------------------------------------------------------- receipts

def trawl_cli(*args, cwd=None, progress=None):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8"}
    env.pop("TRAWL_PROGRESS", None)
    if progress is not None:
        env["TRAWL_PROGRESS"] = progress  # "1" acts as if stderr were a terminal
        env["TRAWL_PROGRESS_AFTER"] = "0.05"  # and the line shows after 0.05 s of quiet, not 1 s
    return subprocess.run([sys.executable, "-m", "trawl", *args], capture_output=True, text=True, encoding="utf-8",
                          timeout=600, cwd=cwd, env=env)


def test_receipt_from_the_interpreter_verifies(tmp_path):
    prog = tmp_path / "fibs.trawl"
    prog.write_text("use stdlib\n10 fib . 20 fib .\n", encoding="utf-8")
    rec = tmp_path / "fibs.receipt"
    r = trawl_cli("run", str(prog), "--receipt", str(rec))
    assert r.returncode == 0 and r.stdout == "55 6765 \n", r.stderr
    import json
    data = json.loads(rec.read_text(encoding="utf-8"))
    assert set(data["program"]["files"]) == {"fibs.trawl", "stdlib.trawl"}  # the library travels with it
    # it stands on its own: the program file can go away
    prog.unlink()
    r = trawl_cli("verify", str(rec), "--engine", "run")
    assert r.returncode == 0 and "✓" in r.stdout and "same output" in r.stdout, r.stdout + r.stderr
    if HAVE_CC:
        r = trawl_cli("verify", str(rec))  # default: the other engine
        assert r.returncode == 0 and "native compiler" in r.stdout, r.stdout + r.stderr
    # a receipt that claims different output fails
    data["output"]["sha256"] = "0" * 64
    data["output"]["head"] = "55 6766 \n"
    rec.write_text(json.dumps(data), encoding="utf-8")
    r = trawl_cli("verify", str(rec), "--engine", "run")
    assert r.returncode == 1 and "the output is different" in r.stdout and "line 1" in r.stdout, r.stdout
    # and so does one whose program was edited afterwards
    data["program"]["files"]["fibs.trawl"] = data["program"]["files"]["fibs.trawl"].replace("20", "21")
    rec.write_text(json.dumps(data), encoding="utf-8")
    r = trawl_cli("verify", str(rec), "--engine", "run")
    assert r.returncode == 1 and "changed after it was made" in r.stdout


@needs_cc
def test_receipt_of_a_saved_sweep_spot_checks_blocks(tmp_path):
    prog = tmp_path / "seven.trawl"
    prog.write_text(SWEEP_PROG, encoding="utf-8")
    rec, save = tmp_path / "seven.receipt", tmp_path / "seven.sav"
    r = trawl_cli("fast", str(prog), "--jobs", "3", "--save", str(save), "--receipt", str(rec))
    assert r.returncode == 0 and r.stdout == interp(SWEEP_PROG), r.stderr
    r = trawl_cli("verify", str(rec), "--save", str(save), "--sample", "5", "--seed", "1")
    assert r.returncode == 0 and "re-ran 5 saved blocks" in r.stdout and "replayed 251" in r.stdout, r.stdout + r.stderr
    # a save file whose blocks were changed is not the one the receipt was made with
    old = b"B 0 0 3000 0 11 1 2\n7 \n"
    bad = tmp_path / "bad.sav"
    bad.write_bytes(save.read_bytes().replace(old, b"B 0 0 3000 0 11 2 2\n7 \n"))
    r = trawl_cli("verify", str(rec), "--save", str(bad), "--sample", "1")
    assert r.returncode == 1 and "not the save file" in r.stdout
    # without the receipt's save stamp, re-running that block still catches the wrong count
    import json
    data = json.loads(rec.read_text(encoding="utf-8"))
    del data["save"]
    rec.write_text(json.dumps(data), encoding="utf-8")
    r = trawl_cli("verify", str(rec), "--save", str(bad), "--sample", "256")
    assert r.returncode == 1 and "saved count 2, re-run count 1" in r.stdout, r.stdout


# ---------------------------------------------------------------- the "still running" line

# about a second on the interpreter, quiet in the middle
SLOW_PROG = '." start" 10 emit\n0 700000 [ swap 1 + swap 1 - ] drop . 10 emit ." end" 10 emit\n'
SLOW_OUT = "start\n700000 \nend\n\n"


def test_a_quiet_run_shows_it_is_still_running_on_stderr_only(tmp_path):
    prog = tmp_path / "slow.trawl"
    prog.write_text(SLOW_PROG, encoding="utf-8")
    rec = tmp_path / "slow.receipt"
    r = trawl_cli("run", str(prog), "--max-steps", "100000000", "--receipt", str(rec), progress="1")
    assert r.returncode == 0, r.stderr
    assert "still running... 0:0" in r.stderr and "(Ctrl+C stops it)" in r.stderr and "~ done in 0:0" in r.stderr, r.stderr
    assert r.stdout == SLOW_OUT  # stdout is only what the program printed
    import json
    data = json.loads(rec.read_text(encoding="utf-8"))
    assert data["output"]["head"] == SLOW_OUT  # and so is the receipt
    # verify's re-run is silent, so it says it is still checking, and says how long the first run took
    r = trawl_cli("verify", str(rec), "--engine", "run", "--max-steps", "100000000", progress="1")
    assert r.returncode == 0 and "✓" in r.stdout and "re-running it on the reference interpreter" in r.stdout, r.stdout
    assert "still checking... 0:0" in r.stderr and "still" not in r.stdout, r.stderr
    # piped into a file (no terminal), nothing extra is printed
    r = trawl_cli("run", str(prog), "--max-steps", "100000000")
    assert r.returncode == 0 and r.stdout == SLOW_OUT and r.stderr == "", r.stderr


def test_the_running_line_is_wiped_before_output_and_never_splits_a_line(monkeypatch):
    import io
    import time
    from trawl import progress
    monkeypatch.setenv("TRAWL_PROGRESS_AFTER", "0.05")
    out, err = io.StringIO(), io.StringIO()
    t = progress.Ticker("still running", err=err, out=out, enabled=True)
    t.shared = True  # stdout and stderr on the same screen
    with t:
        t.write("half a line")
        time.sleep(0.6)
        drawn = err.getvalue()
        t.write(" and the rest\n")
        time.sleep(0.6)
    assert out.getvalue() == "half a line and the rest\n"
    assert drawn.startswith("\n\r") and "still running... 0:00" in drawn, repr(drawn)  # a new line first
    e = err.getvalue()
    assert e.endswith("\r") and e.rstrip("\r").endswith(" " * 10), repr(e[-60:])  # wiped when done
    assert t.drawn == 0
    assert progress.clock(3725) == "1:02:05" and progress.clock(125) == "2:05"
    from trawl.receipt import first_took
    assert first_took({"engine": "run", "seconds": 125.3}) == " (the first run, with trawl run, took 2:05)"
    assert first_took({"engine": "fast", "seconds": 0.2}) == ""


@needs_cc
def test_fast_with_the_running_line_prints_the_same(tmp_path):
    prog = tmp_path / "seven.trawl"
    prog.write_text(SWEEP_PROG, encoding="utf-8")
    r = trawl_cli("fast", str(prog), progress="1")
    assert r.returncode == 0 and r.stdout == interp(SWEEP_PROG) and "~ done in" in r.stderr, r.stderr


# ---------------------------------------------------------------- factorings and today's OEIS examples

def brute_factorings(n, least=2):
    """Ways to write n as a product of factors >= least, in nondecreasing order (independent check)."""
    if n == 1:
        return 1
    return sum(brute_factorings(n // d, d) for d in range(least, n + 1) if n % d == 0)


def test_factorings_counts_unordered_factorizations(engine):
    want = " ".join(str(brute_factorings(n)) for n in range(1, 200)) + " "
    assert engine("1 1 [ drop dup factorings . 1 + dup 200 < ] drop drop") == want + "\n"
    # many primes: checked against the interpreter's count (on the native engine, a cross-check)
    assert engine("2 10 power 3 5 power * 5 3 power * 7 * 11 * 13 * factorings .") == f"{factorings_py(2**10 * 3**5 * 5**3 * 7 * 11 * 13)} \n"
    # one prime: the count is the number of partitions of the exponent (p(100) = 190569292)
    assert engine("2 100 power factorings . 30 factorings . 1 factorings .") == "190569292 5 1 \n"


def factorings_py(n):
    from trawl.interp import factorings
    return factorings(n)


@needs_cc
def test_factorings_reproduces_the_a398813_trajectory(native):
    src = "2 31 power 3 14 power * 5 12 power * 1 [ drop dup . factorings dup 1 - ] drop"
    assert native(src) == "2507653251072000000000000 729108451671600 10920630 2610 36 9 2 \n"


def test_old_programs_keep_their_own_factorings(engine):
    assert engine(": factorings 1 + ; 41 factorings .") == "42 \n"


def a398813_small():
    src = (ROOT / "examples" / "a398813.trawl").read_text(encoding="utf-8")
    big = "2 31 power 3 14 power * 5 12 power * -2 bury  7 -7 bury"
    assert big in src
    return src.replace(big, "454164480 -2 bury  6 -7 bury")


A398813_SMALL_OUT = """0 steps: 1 
1 steps: 2 
2 steps: 4 
3 steps: 12 
4 steps: 48 
5 steps: 1728 
6 steps: 454164480 
up to 454164480 these take 6 or more steps:
454164480 

"""


def test_a398813_example_finds_the_terms(engine):
    # the shipped example proves a(7) the same way up to 2^31 3^14 5^12 (minutes on all cores)
    assert engine(a398813_small(), str(ROOT / "examples" / "a398813.trawl")) == A398813_SMALL_OUT


@needs_cc
def test_a398813_example_on_many_cores(tmp_path):
    exe = str(tmp_path / ("a398" + (".exe" if os.name == "nt" else "")))
    build(parse(a398813_small(), str(ROOT / "examples" / "a398813.trawl")), exe)
    r = subprocess.run([exe, "--jobs", "4"], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and r.stdout == A398813_SMALL_OUT


SPIRAL_100 = ("1 2 1 3 1 4 1 5 1 3 1 6 1 7 1 2 1 8 1 2 1 3 1 9 1 2 1 10 1 2 1 3 1 11 1 3 1 5 1 3 1 6 1 7 1 2 "
              "1 4 1 3 1 12 1 3 1 4 1 5 1 2 1 13 1 2 1 4 1 2 1 14 1 2 1 3 1 15 1 3 1 5 1 2 1 7 1 2 1 16 1 2 1 3 "
              "1 17 1 3 1 6 1 3 ")


def spiral(cells):
    src = (ROOT / "examples" / "spiral_packing.trawl").read_text(encoding="utf-8")
    assert "\n3355518 [" in src
    return src.replace("\n3355518 [", f"\n{cells} [")


def test_spiral_packing_example(engine):
    out = engine(spiral(120))
    first, *records = out.splitlines()
    assert first == SPIRAL_100
    cells = [int(r.split()[-1]) for r in records if r.startswith("value ")]
    assert cells == [1, 2, 4, 6, 8, 12, 14, 18, 24, 28, 34, 52, 62, 70, 76, 88, 94, 106, 116]


@needs_cc
def test_spiral_packing_records_natively(native):
    cells = [int(r.split()[-1]) for r in native(spiral(20000)).splitlines() if r.startswith("value ")]
    assert cells == [1, 2, 4, 6, 8, 12, 14, 18, 24, 28, 34, 52, 62, 70, 76, 88, 94, 106, 116,
                     150, 164, 540, 806, 820, 1212, 1988, 2530, 2734, 5822, 18136]  # values 1 .. 30
