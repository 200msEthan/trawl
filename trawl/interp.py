"""The reference interpreter: slow, careful, and the definition of what Trawl means.

`trawl run` uses it. It checks things the native build skips for speed:
  - a loop that comes back to the exact same sea (stack + seabed) can never end,
    so it stops with a proof instead of hanging
  - a run longer than the step limit stops instead of running forever
The native compiler (trawl/cgen.py) must print exactly what this prints; the test
suite checks that on every program in examples/ and tests/programs/.
"""
import sys

if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)  # Trawl numbers can have any number of digits

from .lang import (Def, If, Loop, Num, PearlDecl, Program, Sweep, Text, TrawlError, WANTS, Word,
                   parse, suggest)


class Sink(Exception):
    """This world is wrong. Rewind."""


class Sea:
    def __init__(self, tape=None, sunk=0, max_steps=50_000_000, stream=None):
        self.stack, self.words, self.out, self.bed = [], {}, [], {}
        self.steps, self.max_steps, self.stream = 0, max_steps, stream
        self.tape, self.pos, self.sunk = ([] if tape is None else tape), 0, sunk
        self.pearls, self.echoes, self.echo_n = {}, {}, 0
        self.sweep_depth, self.plan = 0, None  # plan: replays and spot-checks saved sweep blocks (trawl verify)

    # -- small helpers
    def pop(self, word, want=1):
        if not self.stack:
            raise TrawlError(f"the tide went out too far: `{word}` wanted {want} number{'s' if want > 1 else ''}, the shore was empty")
        return self.stack.pop()

    def say(self, text):
        if self.stream is not None:
            self.stream.write(text)
            if "\n" in text:
                self.stream.flush()
        else:
            self.out.append(text)

    @staticmethod
    def show(v):
        return str(v)

    def zero(self, a):
        raise TrawlError(f"you tried to split {a} into zero pieces. the ocean refuses.")

    def digits(self, word, alternate):
        base, x = self.pop(word, 2), self.pop(word, 2)
        if base < 2:
            raise TrawlError(f"base {base} has no digits. try 2 or more.")
        total, sign, x = 0, 1, abs(x)
        while x:
            x, r = divmod(x, base)
            total += sign * r
            if alternate:
                sign = -sign
        self.stack.append(total)

    # -- running
    def run(self, prog):
        self.exec(prog.body)
        return "".join(self.out)

    def exec(self, body):
        st = self.stack
        for node in body:
            self.steps += 1
            if self.steps > self.max_steps:
                raise TrawlError(f"the tide never went out: {self.max_steps // 1_000_000} million steps and still going")
            k = type(node)
            if k is Num:
                st.append(node.value)
            elif k is Word:
                self.word(node)
            elif k is Loop:
                self.loop(node)
            elif k is If:
                cond = self.pop("if")
                if cond:
                    self.exec(node.then)
                elif node.other is not None:
                    self.exec(node.other)
            elif k is Text:
                self.say(node.text)
            elif k is Def:
                self.words[node.name] = node.body
            elif k is PearlDecl:
                self.pearls.setdefault(node.name, {})
            elif k is Sweep:
                self.sweep(node)

    def loop(self, node):
        seen = set()
        st = self.stack
        while st and st[-1] != 0:
            # The sea is deterministic: if a loop comes back to the exact same stack and
            # seabed, it will repeat forever. Proof, not a guess. The picture holds
            # everything a pass can see: `echo` remembers past looks and `wave` reads the
            # guess tape, so both go in too (a loop that calls them never repeats a picture,
            # and the step limit stops it instead of a wrong proof).
            if len(st) + len(self.bed) + len(self.echoes) <= 256:
                sea = (tuple(st), len(self.words), tuple(sorted(self.bed.items())),
                       self.echo_n, tuple(sorted(self.echoes.items())), self.pos)
                if sea in seen:
                    raise TrawlError(f"the tide is stuck: this loop came back to the exact same sea {list(st)}, so it can never end")
                if len(seen) < 50_000:
                    seen.add(sea)
            self.exec(node.body)

    def sweep(self, node):
        name, st = node.name, self.stack
        hi, lo = self.pop("sweep", 2), self.pop("sweep", 2)
        if name not in self.words:
            raise TrawlError(f"{_where(node)}nobody in the sea knows the word `{name}` yet, so `sweep` can't run it.")
        check_sweep_range(lo, hi)
        call = Word(name, node.tok)

        def block(a, b):
            count = 0
            self.sweep_depth += 1
            try:
                for n in range(a, b):
                    depth = len(st)
                    st.append(n)
                    self.word(call)
                    if len(st) != depth + 1:
                        raise TrawlError(sweep_shape_error(name, n, len(st) - depth))
                    if st.pop():
                        count += 1
            finally:
                self.sweep_depth -= 1
            return count

        if self.plan is not None and self.sweep_depth == 0 and lo < hi:
            st.append(self.plan.sweep(self, block, lo, hi))
        else:
            st.append(block(lo, hi))

    def capture(self, fn):
        """Run fn with everything it says kept aside; returns (text, fn's result)."""
        stream, out = self.stream, self.out
        self.stream, self.out = None, []
        try:
            result = fn()
            return "".join(self.out), result
        finally:
            self.stream, self.out = stream, out

    def word(self, node):
        t, st = node.name, self.stack
        if t in self.words:
            if t in self.pearls:
                n, shell = self.pop(t), self.pearls[t]
                if n in shell:
                    st.append(shell[n])
                else:
                    st.append(n)
                    self.exec(self.words[t])
                    shell[n] = st[-1] if st else self.pop(t)
            else:
                self.exec(self.words[t])
            return
        if t in ("+", "-", "*", "/", "%", "=", "<", ">"):
            b, a = self.pop(t, 2), self.pop(t, 2)
            if t == "+": st.append(a + b)
            elif t == "-": st.append(a - b)
            elif t == "*": st.append(a * b)
            elif t == "/": st.append(a // b if b else self.zero(a))
            elif t == "%": st.append(a % b if b else self.zero(a))
            elif t == "=": st.append(int(a == b))
            elif t == "<": st.append(int(a < b))
            else: st.append(int(a > b))
        elif t == "dup":
            v = self.pop(t); st += [v, v]
        elif t == "drop":
            self.pop(t)
        elif t == "swap":
            b, a = self.pop(t, 2), self.pop(t, 2); st += [b, a]
        elif t == "over":
            b, a = self.pop(t, 2), self.pop(t, 2); st += [a, b, a]
        elif t == "rot":
            c, b, a = self.pop(t, 3), self.pop(t, 3), self.pop(t, 3); st += [b, c, a]
        elif t == "fold":
            st.reverse()
        elif t == "depth":
            st.append(len(st))
        elif t == ".":
            self.say(self.show(self.pop(t)) + " ")
        elif t == "emit":
            c = self.pop(t)
            if not 0 <= c <= 0x10FFFF:
                raise TrawlError(f"emit wants a character number from 0 to 1114111, not {c}")
            if 0xD800 <= c <= 0xDFFF:
                raise TrawlError(f"emit can't print {c}: 55296 to 57343 are not characters (they are halves of UTF-16 pairs)")
            self.say(chr(c))
        elif t == ".s":
            self.say("<" + " ".join(map(str, st)) + "> ")
        elif t == ".bed":
            self.say("{" + " ".join(f"{k}:{v}" for k, v in sorted(self.bed.items())) + "} ")
        elif t == ".pearls":
            self.say(f"({sum(map(len, self.pearls.values()))} pearls) ")
        elif t == "bury":
            addr, v = self.pop(t, 2), self.pop(t, 2)
            if v == 0:
                self.bed.pop(addr, None)
            else:
                self.bed[addr] = v
        elif t == "dig":
            st.append(self.bed.get(self.pop(t), 0))
        elif t == "drain":
            self.bed.clear()
        elif t == "digsum":
            self.digits(t, False)
        elif t == "altsum":
            self.digits(t, True)
        elif t == "prime":
            st.append(1 if is_prime(self.pop(t)) else 0)
        elif t == "spf":
            n = self.pop(t)
            if n < 2:
                raise TrawlError(f"spf needs a whole number of 2 or more, not {n}")
            st.append(smallest_prime_factor(n))
        elif t == "factorings":
            n = self.pop(t)
            if n < 1:
                raise TrawlError(f"factorings needs a whole number of 1 or more, not {n}")
            st.append(factorings(n))
        elif t == "power":
            k, a = self.pop(t, 2), self.pop(t, 2)
            if k < 0:
                raise TrawlError(f"tide numbers are whole: {a} to the power {k} would be a fraction")
            st.append(a ** k)
        elif t == "echo":
            hi, lo = self.pop(t, 2), self.pop(t, 2)
            key = tuple(self.bed.get(a, 0) for a in range(lo, hi + 1))
            st.append(self.echo_n - self.echoes[key] if key in self.echoes else 0)
            self.echoes[key] = self.echo_n
            self.echo_n += 1
        elif t == "hush":
            self.echoes, self.echo_n = {}, 0
        elif t == "wave":
            hi, lo = self.pop(t, 2), self.pop(t, 2)
            if lo > hi:
                raise Sink()  # an empty range: no world can live here
            if self.pos == len(self.tape):
                self.tape.append([lo, hi, lo])
            st.append(self.tape[self.pos][2])
            self.pos += 1
        elif t == "want":
            if not self.pop(t):
                raise Sink()
        elif t == "more":
            raise Sink(True)
        elif t == "aim":
            raise Sink(("aim", self.pop(t)))
        elif t == "worlds":
            st.append(self.sunk)
        else:
            raise TrawlError(f"{_where(node)}nobody in the sea knows the word `{t}`.{suggest(t, self.words)}")



# ---------------------------------------------------------------- primes
# `prime` runs strong probable-prime tests to the 13 prime bases 2..41, which is exact for every
# n below 3.3 * 10^24 (Sorenson and Webster, 2015). The native runtime runs the same tests, so the
# two engines always agree. `spf` finds the smallest prime factor with trial division, then
# Pollard-Brent rho.
_BASES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41)
# 3317044064679887385961981 is the smallest composite that passes all 13 (it is also where the
# exact range ends). From there up, seven more bases, so that number and its kind are caught.
_EXACT_BELOW = 3317044064679887385961981
_MORE_BASES = _BASES + (43, 47, 53, 59, 61, 67, 71)


def is_prime(n):
    if n < 2:
        return False
    for p in _BASES:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (_BASES if n < _EXACT_BELOW else _MORE_BASES):
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _rho(n):
    from math import gcd
    c = 1
    while True:
        y, r, q, g = 2, 1, 1, 1
        while g == 1:
            x = y
            for _ in range(r):
                y = (y * y + c) % n
            k = 0
            while k < r and g == 1:
                ys = y
                for _ in range(min(128, r - k)):
                    y = (y * y + c) % n
                    q = q * abs(x - y) % n
                g = gcd(q, n)
                k += 128
            r *= 2
        if g == n:
            g = 1
            while g == 1:
                ys = (ys * ys + c) % n
                g = gcd(abs(x - ys), n)
        if g != n:
            return g
        c += 1


def signature(n):
    """The exponents of n's prime factorization, largest first: 720 = 2^4 3^2 5 -> (4, 2, 1)."""
    exps = []
    while n > 1:
        p, e = smallest_prime_factor(n), 0
        while n % p == 0:
            n //= p
            e += 1
        exps.append(e)
    return tuple(sorted(exps, reverse=True))


_FACTORINGS = {(): 1}


def _factorings_of(v):
    # Unordered factorizations depend only on the exponents. With v[0] the largest exponent,
    # v[0] f(v) = sum over nonzero w <= v of c(w) f(v - w), where c(w) = sum over d | gcd(w) of w[0] / d.
    # (Differentiate the Euler product for the generating function with respect to the first prime.)
    if v in _FACTORINGS:
        return _FACTORINGS[v]
    from itertools import product
    from math import gcd
    total = 0
    for w in product(*[range(a + 1) for a in v]):
        g = 0
        for x in w:
            g = gcd(g, x)
        if g == 0:
            continue
        c = sum(w[0] // d for d in range(1, g + 1) if g % d == 0)
        if c:
            rest = tuple(sorted((a - b for a, b in zip(v, w) if a > b), reverse=True))
            total += c * _factorings_of(rest)
    _FACTORINGS[v] = total // v[0]
    return _FACTORINGS[v]


def factorings(n):
    """How many ways n is a product of whole numbers above 1, order ignored (OEIS A001055)."""
    return _factorings_of(signature(n))


def smallest_prime_factor(n):
    if n % 2 == 0:
        return 2
    i = 3
    while i < 1000:
        if i * i > n:
            return n
        if n % i == 0:
            return i
        i += 2
    if is_prime(n):
        return n
    f = _rho(n)
    return min(smallest_prime_factor(f), smallest_prime_factor(n // f))


def _where(node):
    import os
    return f"{os.path.basename(node.tok.file)}:{node.tok.line}: "


SWEEP_MIN, SWEEP_MAX = -(1 << 62), (1 << 62) - 1


def check_sweep_range(lo, hi):
    if not (SWEEP_MIN <= lo <= SWEEP_MAX and SWEEP_MIN <= hi <= SWEEP_MAX):
        raise TrawlError(f"`sweep` runs from {lo} up to {hi}, but both ends must lie between -2^62 and 2^62 - 1")


def sweep_shape_error(name, n, got):
    return (f"`sweep {name}` wants `{name}` to turn each n into one answer, "
            f"but at n = {n} it left {got} number{'s' if got != 1 else ''} in its place")


def uses_worlds(prog):
    def walk(body):
        for n in body:
            if isinstance(n, Word) and n.name in ("wave", "want", "more", "aim"):
                return True
            for sub in (getattr(n, "body", None), getattr(n, "then", None), getattr(n, "other", None)):
                if sub and walk(sub):
                    return True
        return False
    return walk(prog.body)


# Words calling words nest Python calls, and Python's default limit (1000 frames) stopped
# `trawl run` near 330 levels while `trawl fast` went millions deep. So a run gets its own
# thread with a big stack and a high frame limit; past that, a plain `~` error, not a traceback.
DEEP_FRAMES = 100_000
_DEEP_STACK = 255 * 1024 * 1024  # Windows wants a thread's stack below 256 MB


def _deep(fn):
    import threading
    if getattr(_deep, "inside", False):
        return fn()
    box = {}

    def work():  # only this thread has the big stack, so only it gets the high frame limit
        _deep.inside, limit = True, sys.getrecursionlimit()
        sys.setrecursionlimit(max(limit, DEEP_FRAMES))
        try:
            box["value"] = fn()
        except BaseException as e:  # carried back to the caller's thread
            box["error"] = e
        finally:
            _deep.inside = False
            sys.setrecursionlimit(limit)
    try:
        old = threading.stack_size(_DEEP_STACK)
    except (ValueError, RuntimeError):  # no big stacks here: run as is
        return fn()
    try:
        t = threading.Thread(target=work, daemon=True)
        t.start()
    except RuntimeError:  # no threads at all (the playground, in the browser): run as is
        return fn()
    finally:
        threading.stack_size(old)
    while t.is_alive():
        t.join(0.2)  # a short wait, so Ctrl+C still reaches the main thread
    if "error" in box:
        raise box["error"]
    return box["value"]


def _too_deep(fn):
    try:
        return fn()
    except RecursionError:
        raise TrawlError("the sea got too deep: words called inside words too many levels down. "
                         "does a word call itself forever?") from None


def run(prog, max_steps=50_000_000, max_worlds=2_000_000, stream=None, plan=None):
    """Run a parsed program. Returns (printed text, worlds sunk or None)."""
    return _deep(lambda: _too_deep(lambda: _run(prog, max_steps, max_worlds, stream, plan)))


def _run(prog, max_steps, max_worlds, stream, plan):
    if not uses_worlds(prog):
        sea = Sea(max_steps=max_steps, stream=stream)
        sea.plan = plan
        return sea.run(prog), None
    tape, kept, sunk, best = [], [], 0, None
    while True:
        w = Sea(tape, sunk, max_steps=max_steps)
        try:
            w.run(prog)
            return "".join(kept) + "".join(w.out), sunk
        except Sink as s:
            if s.args and s.args[0] is True:  # `more`: this world was good, keep its words
                kept.append("".join(w.out))
            elif s.args:  # `aim`: keep it only if it beats the best so far
                score = s.args[0][1]
                if best is None or score < best[0]:
                    best = (score, "".join(w.out))
            del tape[w.pos:]  # guesses made after the sinking point never happened
            while tape and tape[-1][2] == tape[-1][1]:
                tape.pop()  # this guess has tried every number: rewind further
            sunk += 1
            if not tape:
                if best is not None:
                    return "".join(kept) + best[1], sunk
                if kept:
                    return "".join(kept), sunk
                raise TrawlError(f"every world sank ({sunk} of them). the sea has no answer.")
            if sunk > max_worlds:
                raise TrawlError(f"{max_worlds} worlds sank and still no shore")
            tape[-1][2] += 1


def run_source(src, file="<program>", **kw):
    return run(parse(src, file), **kw)
