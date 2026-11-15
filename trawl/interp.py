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

from .lang import (Def, If, Loop, Num, PearlDecl, Program, Text, TrawlError, WANTS, Word,
                   parse, suggest)


class Sink(Exception):
    """This world is wrong. Rewind."""


class Sea:
    def __init__(self, tape=None, sunk=0, max_steps=50_000_000, stream=None):
        self.stack, self.words, self.out, self.bed = [], {}, [], {}
        self.steps, self.max_steps, self.stream = 0, max_steps, stream
        self.tape, self.pos, self.sunk = ([] if tape is None else tape), 0, sunk
        self.pearls, self.echoes, self.echo_n = {}, {}, 0

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

    def loop(self, node):
        seen = set()
        st = self.stack
        while st and st[-1] != 0:
            # The sea is deterministic: if a loop comes back to the exact same stack and
            # seabed, it will repeat forever. Proof, not a guess.
            if len(st) + len(self.bed) <= 256:
                sea = (tuple(st), len(self.words), tuple(sorted(self.bed.items())))
                if sea in seen:
                    raise TrawlError(f"the tide is stuck: this loop came back to the exact same sea {list(st)}, so it can never end")
                if len(seen) < 50_000:
                    seen.add(sea)
            self.exec(node.body)

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
    for a in _BASES:
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


def run(prog, max_steps=50_000_000, max_worlds=2_000_000, stream=None):
    """Run a parsed program. Returns (printed text, worlds sunk or None)."""
    if not uses_worlds(prog):
        sea = Sea(max_steps=max_steps, stream=stream)
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
