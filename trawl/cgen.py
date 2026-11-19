"""The native compiler: Trawl -> C -> machine code.

Each Trawl word becomes one C function `long w(long sp)` that works on a shared stack.
Inside a straight run of words the compiler keeps the top of the stack in C variables
instead of memory, so `dup * 1 +` turns into a few register operations. Numbers are
62-bit machine words that switch to exact big numbers on overflow (trawl_rt.h), so the
native build prints exactly what the reference interpreter prints.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

from .lang import (Def, If, Loop, Num, PearlDecl, Program, Sweep, Text, TrawlError, WANTS, Word,
                   BUILTINS, YOURS_WINS, shown, special_hint, suggest, where)

RUNTIME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
SMALL_MIN, SMALL_MAX = -(1 << 62), (1 << 62) - 1
DIRECT_LO, DIRECT_HI = -65535, (1 << 22)  # constant seabed addresses reached without a lookup

BINOPS = {"+": "v_add", "-": "v_sub", "*": "v_mul", "/": "v_div", "%": "v_mod",
          "=": "v_eq", "<": "v_lt", ">": "v_gt"}


def fold(op, a, b):
    """Constant-fold an arithmetic word; None when it must wait for run time (division by zero)."""
    if op == "+": return a + b
    if op == "-": return a - b
    if op == "*": return a * b
    if op in "/%" and b == 0: return None
    if op == "/": return a // b
    if op == "%": return a % b
    if op == "=": return int(a == b)
    if op == "<": return int(a < b)
    return int(a > b)


def c_string(s):
    out = []
    for byte in s.encode("utf-8"):
        ch = chr(byte)
        if ch in '"\\?':
            out.append("\\" + ch)
        elif 32 <= byte < 127:
            out.append(ch)
        else:
            out.append("\\%03o" % byte)
    return '"' + "".join(out) + '"'


def c_name(word):
    return "".join(c if c.isalnum() else "_" for c in word)[:40]


class Item:
    __slots__ = ("expr", "const", "slot")

    def __init__(self, expr, const=None, slot=None):
        self.expr, self.const, self.slot = expr, const, slot


class Fn:
    """Code for one C function, with the cached top of the stack.

    Two speed tricks live here:
      - the top of the stack stays in C variables until something needs memory
      - inside loops, seabed cells at fixed addresses (the usual Trawl variables,
        like `-7 dig`) live in C variables too; they are written back before anything
        that could look at the seabed (a call, echo, .bed, a computed address that
        lands on them) and read again after anything that could change it
    """

    def __init__(self, gen):
        self.gen, self.lines, self.pad, self.vs, self.under, self.nt = gen, [], "  ", [], 0, 0
        self.known = 0  # numbers surely on the memory stack below the cached ones
        self.loops = []  # ids of the loops we are inside, outermost first

    def emit(self, line):
        self.lines.append(self.pad + line)

    def mark(self, kind, arg):
        self.lines.append((kind, arg, self.pad))

    def temp(self, expr):
        self.nt += 1
        name = f"t{self.nt}"
        self.emit(f"V {name} = {expr};")
        return Item(name)

    def push(self, item):
        self.vs.append(item)

    def const(self, n):
        return Item(self.gen.literal(n), const=n)

    def pop(self, word, want):
        if self.vs:
            return self.vs.pop()
        self.under += 1
        k = self.under
        if k > self.known:
            self.emit(f"if (UNLIKELY(sp < {k})) rt_underflow({c_string(word)}, {want});")
        it = self.temp(f"S[sp - {k}]")
        it.slot = -k
        return it

    def depth_expr(self):
        d = len(self.vs) - self.under
        return f"MKV(sp + {d})" if d >= 0 else f"MKV(sp - {-d})"

    def flush(self, extra=0):
        n, u = len(self.vs), self.under
        if n + extra > u:
            self.emit(f"ENSURE(sp + {n + extra - u});")
        for i, it in enumerate(self.vs):
            rel = i - u
            if it.slot == rel:
                continue
            self.emit(f"S[sp {'+' if rel >= 0 else '-'} {abs(rel)}] = {it.expr};")
        if n + extra != u:
            self.emit(f"sp += {n + extra - u};")
        self.known = max(self.known, u) - u + n + extra
        self.vs, self.under = [], 0

    def open(self, line):
        self.emit(line)
        self.pad += "  "

    def close(self, line="}"):
        self.pad = self.pad[:-2]
        self.emit(line)

    # -- seabed cells at fixed addresses
    def cell_name(self, c):
        L = self.loops[0]
        cells = self.gen.loop_cells[L]
        if c not in cells:
            cells[c] = f"c{L}_{len(cells)}"
        self.gen.promoted.add(c)
        return cells[c]

    def dig_const(self, c):
        if not self.loops:
            return self.temp(f"bed[{c} + BED_OFF]")
        return self.temp(self.cell_name(c))

    def bury_const(self, c, v):
        if not self.loops:
            self.emit(f"bed[{c} + BED_OFF] = {v};")
            return
        name = self.cell_name(c)
        self.gen.loop_written[self.loops[0]].add(c)
        self.emit(f"{name} = {v};")

    def spill(self):
        if self.loops:
            self.mark("spill", self.loops[0])

    def reload(self):
        if self.loops:
            self.mark("reload", self.loops[0])

    def guard(self, addr):
        return f"UNLIKELY((uint64_t)({addr} - MKV(@@GLO@@)) <= (uint64_t)(MKV(@@GHI@@) - MKV(@@GLO@@)))"


class Gen:
    def __init__(self, prog, file="<program>"):
        self.prog, self.file = prog, file
        self.consts, self.const_index = [], {}
        self.direct = set()
        self.by_name = {}
        for i, d in enumerate(prog.defs):
            self.by_name.setdefault(d.name, []).append(i)
        self.def_index = {id(d): i for i, d in enumerate(prog.defs)}
        # Words defined more than once with different bodies. The interpreter calls whichever
        # meaning is in force when the call happens, so the compiled code keeps a "current
        # meaning" number for each of them, set where each definition is reached.
        self.varied = {name for name, idxs in self.by_name.items()
                       if len({self.shape(prog.defs[i].body) for i in idxs}) > 1}
        self.pearl_ids = {}
        self.worlds = False
        self.def_seen = 0
        self.sure, self.cond = set(), 0  # names surely defined by now at the top level; inside an if/loop?
        self.nloops = 0
        self.loop_cells, self.loop_written = {}, {}
        self.promoted = set()

    # -- values
    def literal(self, n):
        if SMALL_MIN <= n <= SMALL_MAX:
            v = 2 * n
            return "((V)(-9223372036854775807LL - 1))" if v == -(1 << 63) else f"((V){v}LL)"
        if n not in self.const_index:
            self.const_index[n] = len(self.consts)
            self.consts.append(n)
        return f"K[{self.const_index[n]}]"

    def shape(self, body):
        out = []
        for n in body:
            if isinstance(n, Word):
                out.append(n.name + "@" + where(n.tok))  # where it is matters: an unknown word's error names its line
            elif isinstance(n, Num):
                out.append(str(n.value))
            elif isinstance(n, Text):
                out.append("\x00." + n.text + "\x00")  # never the same as a word's name
            elif isinstance(n, Loop):
                out.append("\x00[ " + self.shape(n.body) + " \x00]")  # never the same as a word like `[x]`
            elif isinstance(n, If):
                out.append("\x00if( " + self.shape(n.then) + " \x00| " + (self.shape(n.other) if n.other is not None else "") + " \x00)")
            elif isinstance(n, Def):
                out.append("\x00: " + n.name + " \x00( " + self.shape(n.body) + " \x00)")
            elif isinstance(n, PearlDecl):
                out.append("\x00pearl " + n.name)
            elif isinstance(n, Sweep):
                out.append("\x00sweep " + n.name + "@" + where(n.tok))
        return " ".join(out)

    def fn_name(self, i):
        return f"w{i}_{c_name(self.prog.defs[i].name)}"

    # -- which words are copied into their callers instead of called
    def plan_inlining(self):
        n = len(self.prog.defs)
        calls = [set() for _ in range(n)]
        plain = [True] * n

        def walk(body, i):
            size = 0
            for node in body:
                size += 1
                if isinstance(node, (Word, Sweep)):
                    calls[i].update(self.meanings(node.name))
                elif isinstance(node, Loop):
                    size += walk(node.body, i)
                elif isinstance(node, If):
                    size += walk(node.then, i) + (walk(node.other, i) if node.other else 0)
                elif isinstance(node, (Def, PearlDecl)):
                    plain[i] = False
            return size

        sizes = [walk(d.body, i) for i, d in enumerate(self.prog.defs)]

        def reaches(start):
            seen, todo = set(), list(calls[start])
            while todo:
                j = todo.pop()
                if j == start:
                    return True
                if j not in seen:
                    seen.add(j)
                    todo += calls[j]
            return False

        self.inline = [False] * n
        total = {}

        def expanded(i, depth=0):
            if i in total:
                return total[i]
            t = sizes[i] + sum(expanded(j, depth + 1) for j in calls[i] if self.inline[j])
            total[i] = t
            return t

        order = sorted(range(n), key=lambda i: len(calls[i]))
        for _ in range(3):  # leaves first, then words built from them
            for i in order:
                if self.inline[i] or not plain[i] or self.prog.defs[i].name in self.varied or self.prog.defs[i].name in YOURS_WINS or self.prog.defs[i].name in self.pearl_names or self.prog.defs[i].name in self.sweep_names or reaches(i):
                    continue
                total.clear()
                if expanded(i) <= 80:
                    self.inline[i] = True

    def reachable(self):
        """Definitions the program can actually reach; unused library words are skipped."""
        seen, todo = set(), []

        def walk(body, at, top):
            for n in body:
                if isinstance(n, Def) and top:
                    at += 1
                elif isinstance(n, (Word, Sweep)):
                    for j in self.meanings(n.name):  # any meaning may be the one in force
                        if j not in seen:
                            seen.add(j)
                            todo.append(j)
                elif isinstance(n, Loop):
                    at = walk(n.body, at, top)
                elif isinstance(n, If):
                    at = walk(n.then, at, top)
                    if n.other:
                        at = walk(n.other, at, top)
            return at

        walk(self.prog.body, 0, True)
        while todo:
            j = todo.pop()
            walk(self.prog.defs[j].body, j + 1, False)
        return seen

    # -- code
    def body(self, f, nodes, at, top_level):
        for node in nodes:
            k = type(node)
            if k is Num:
                f.push(f.const(node.value))
            elif k is Text:
                f.emit(f"rt_out({c_string(node.text)}, {len(node.text.encode('utf-8'))});")  # the length, not strlen: text may hold a NUL
            elif k is Def:
                if top_level:
                    self.def_seen += 1
                    if not self.cond and node.name not in self.varied:
                        self.sure.add(node.name)
                f.emit(f"{self.state(node.name)} = {self.def_index[id(node)]};")
            elif k is PearlDecl:
                msg = c_string(f"{where(node.tok)}: `pearl {shown(node.name)}`: nobody in the sea knows the word `{shown(node.name)}` yet")
                if node.name not in self.by_name:
                    f.flush()
                    f.emit(f'rt_die("%s", {msg});')
                else:
                    if node.name not in self.sure:
                        f.flush()
                        f.emit(f'if ({self.state(node.name)} < 0) rt_die("%s", {msg});')
                    pid = self.pearl_ids.setdefault(node.name, len(self.pearl_ids))
                    f.emit(f"shells[{pid}].on = 1;")
            elif k is If or k is Loop:
                self.cond += 1  # a definition in here may or may not run
                try:
                    (self.if_ if k is If else self.loop)(f, node, at, top_level)
                finally:
                    self.cond -= 1
            elif k is Word:
                self.word(f, node, self.at(at, top_level), top_level)
            elif k is Sweep:
                self.sweep(f, node, self.at(at, top_level), top_level)

    def at(self, at, top_level):
        return self.def_seen if top_level else at

    def if_(self, f, node, at, top_level):
        cond = f.pop("if", 1)
        if cond.const is not None:
            chosen = node.then if cond.const else node.other
            f.flush()
            if chosen:
                f.open("{")
                self.body(f, chosen, self.at(at, top_level), top_level)
                f.flush()
                f.close()
            return
        f.flush()
        known = f.known
        f.open(f"if ({cond.expr} != 0) {{")
        self.body(f, node.then, self.at(at, top_level), top_level)
        f.flush()
        k1 = f.known
        k2 = known
        if node.other is not None:
            f.close("} else {")
            f.pad += "  "
            f.known = known
            self.body(f, node.other, self.at(at, top_level), top_level)
            f.flush()
            k2 = f.known
        f.close()
        f.known = min(k1, k2)

    def loop(self, f, node, at, top_level):
        # The loop's flag (the top of the stack) lives in a C variable between passes,
        # so `cond [ drop ... cond ]` never stores the flag just to read it back.
        f.flush()
        self.nloops += 1
        L = self.nloops
        outer = not f.loops
        if outer:
            self.loop_cells[L], self.loop_written[L] = {}, set()
            f.mark("pre", L)
        f.loops.append(L)
        lf = f"lf{L}"
        f.emit(f"V {lf} = sp > 0 ? S[sp - 1] : 0;")
        f.open(f"while ({lf} != 0) {{")
        f.open("if (UNLIKELY(gc_due)) {")
        f.emit(f"S[sp - 1] = {lf};")
        f.spill()
        f.emit("gc_run(sp);")
        f.close()
        # the flag's memory slot is counted in sp but may not be written yet
        f.vs, f.under, f.known = [Item(lf)], 1, 1
        self.body(f, node.body, self.at(at, top_level), top_level)
        if f.vs:
            top = f.vs.pop()
            f.flush(extra=1)  # the flag's slot is counted but not written
            f.emit(f"{lf} = {top.expr};")
        else:
            f.flush()
            f.emit(f"{lf} = sp > 0 ? S[sp - 1] : 0;")
        f.close()
        f.emit(f"if (sp > 0) S[sp - 1] = {lf};")
        f.loops.pop()
        if outer:
            f.mark("post", L)
        f.known = 0

    def call(self, f, node, target):
        f.flush()
        f.spill()
        fn = self.fn_name(target)
        t = node.name
        if t in self.pearl_names:
            pid = self.pearl_ids.setdefault(t, len(self.pearl_ids))
            f.emit(f"sp = rt_call_pearl({pid}, {fn}, {c_string(t)}, sp);")
        else:
            f.emit(f"sp = {fn}(sp);")
        f.reload()
        f.known = 0

    def dispatch(self, f, node):
        """Call a word that has several meanings: the one in force right now."""
        t = node.name
        f.flush()
        f.spill()
        f.emit(f"switch (cur{self.by_name[t][0]}) {{")
        for i in self.by_name[t]:
            fn = self.fn_name(i)
            if t in self.pearl_names:
                pid = self.pearl_ids.setdefault(t, len(self.pearl_ids))
                f.emit(f"case {i}: sp = rt_call_pearl({pid}, {fn}, {c_string(t)}, sp); break;")
            else:
                f.emit(f"case {i}: sp = {fn}(sp); break;")
        if t in YOURS_WINS:
            f.emit("default: {")
            self.builtin(f, node)
            f.flush()
            f.emit("}")
        else:
            f.emit(f"default: {self.die_unknown(node)}")
        f.emit("}")
        f.reload()
        f.known = 0

    def sweep(self, f, node, at, top_level):
        t = node.name
        f.flush()
        msg = f"{where(node.tok)}: nobody in the sea knows the word `{shown(t)}` yet, so `sweep` can't run it."
        if t not in self.by_name:
            f.emit(f"if (sp < 2) rt_underflow(\"sweep\", 2);")
            f.emit(f"rt_die(\"%s\", {c_string(msg)});")
            return
        f.spill()
        pid = self.pearl_ids.setdefault(t, len(self.pearl_ids)) if t in self.pearl_names else -1
        f.emit("{ long (*fp)(long) = 0;")
        f.emit(f"switch ({self.state(t)}) {{")
        for i in self.meanings(t):
            for j in (self.by_name[t] if t not in self.varied else [i]):
                f.emit(f"case {j}: fp = {self.fn_name(i)}; break;")
        f.emit("}")
        if len(self.by_name[t]) > 1:
            # the word can be defined again while the sweep runs: look it up for every n, as the interpreter does
            f.emit(f"if (fp) fp = {self.sweep_caller(t)};")
        f.emit(f"if (!fp) {{ if (sp < 2) rt_underflow(\"sweep\", 2); rt_die(\"%s\", {c_string(msg)}); }}")
        f.emit(f"sp = rt_sweep({pid}, fp, {c_string(t)}, sp); }}")
        f.reload()
        f.known = 0

    def sweep_caller(self, t):
        """A function that calls t's meaning at the moment of each call."""
        if t not in self.sweep_callers:
            name = f"sweepcall{len(self.sweep_callers)}_{c_name(t)}"
            lines = [f"static long {name}(long sp) {{", f"  switch ({self.state(t)}) {{"]
            for i in self.meanings(t):
                for j in (self.by_name[t] if t not in self.varied else [i]):
                    lines.append(f"  case {j}: return {self.fn_name(i)}(sp);")
            lines += ["  }", "  return sp;", "}"]
            self.sweep_callers[t] = (name, lines)
        return self.sweep_callers[t][0]

    # -- calling your own words. The interpreter looks a word up when the call happens, so a
    # word can be defined late, defined again, defined inside another word or inside an `if`.
    # Each name keeps a "current meaning" number, set where each definition is reached.
    def state(self, name):
        return f"cur{self.by_name[name][0]}"

    def unknown(self, node, extra=""):
        t = node.name
        return f"{where(node.tok)}: nobody in the sea knows the word `{shown(t)}`{extra}"

    def die_unknown(self, node):
        """C that stops with the interpreter's message, whose "did you mean" names only a builtin or a word
        defined at the moment of the call: the best match among those is picked when the call happens."""
        import difflib
        t = node.name
        if special_hint(t):  # a hint about comments or whole numbers, not a near name
            return f'rt_die("%s", {c_string(self.unknown(node, "." + suggest(t, ())))});'
        ranked = difflib.get_close_matches(t, list(BUILTINS) + sorted(set(self.by_name) - {t}), 1 << 20, 0.6)
        code = []
        for name in ranked:
            msg = c_string(self.unknown(node, f". did you mean `{name}`?"))
            if name in BUILTINS:  # always there: the search stops here
                code.append(f'rt_die("%s", {msg});')
                return " else ".join(code)
            code.append(f'if ({self.state(name)} >= 0) rt_die("%s", {msg});')
        code.append(f'rt_die("%s", {c_string(self.unknown(node, "."))});')
        return " else ".join(code)

    def meanings(self, name):
        """The definitions a call to name can land on (all of them for a word defined differently twice)."""
        idxs = self.by_name.get(name, [])
        return list(idxs) if name in self.varied else idxs[-1:]

    def word(self, f, node, at, top_level):
        t = node.name
        if t not in self.by_name:
            return self.builtin(f, node)
        if t in self.varied:
            return self.dispatch(f, node)
        target = self.by_name[t][-1]
        sure = top_level and t in self.sure  # defined above, outside any if or loop: no check needed
        if not sure and t in YOURS_WINS:
            # before the program's own definition runs, the built-in answers, as in the interpreter
            f.flush()
            f.spill()
            f.emit(f"if ({self.state(t)} >= 0) {{")
            self.call(f, node, target)
            f.emit("} else {")
            self.builtin(f, node)
            f.flush()
            f.emit("}")
            f.reload()
            f.known = 0
            return
        if not sure:
            f.flush()
            f.emit(f"if (UNLIKELY({self.state(t)} < 0)) {{ {self.die_unknown(node)} }}")
        if self.inline[target]:
            self.body(f, self.prog.defs[target].body, target + 1, False)
        else:
            self.call(f, node, target)

    def builtin(self, f, node):
        t = node.name
        if t in BINOPS:
            b, a = f.pop(t, 2), f.pop(t, 2)
            if a.const is not None and b.const is not None:
                r = fold(t, a.const, b.const)
                if r is not None:
                    f.push(f.const(r))
                    return
            f.push(f.temp(f"{BINOPS[t]}({a.expr}, {b.expr})"))
        elif t == "dup":
            x = f.pop(t, 1); f.push(x); f.push(x)
        elif t == "drop":
            f.pop(t, 1)
        elif t == "swap":
            b, a = f.pop(t, 2), f.pop(t, 2); f.push(b); f.push(a)
        elif t == "over":
            b, a = f.pop(t, 2), f.pop(t, 2); f.push(a); f.push(b); f.push(a)
        elif t == "rot":
            c, b, a = f.pop(t, 3), f.pop(t, 3), f.pop(t, 3); f.push(b); f.push(c); f.push(a)
        elif t == "depth":
            f.push(f.temp(f.depth_expr()))
        elif t == "fold":
            f.flush(); f.emit("rt_fold(sp);")
        elif t == ".s":
            f.flush(); f.emit("rt_dots(sp);")
        elif t == ".":
            f.emit(f"rt_print({f.pop(t, 1).expr});")
        elif t == "emit":
            f.emit(f"rt_emit({f.pop(t, 1).expr});")
        elif t == ".bed":
            f.spill(); f.emit("rt_dotbed();")
        elif t == ".pearls":
            f.emit("rt_dotpearls();")
        elif t == "bury":
            addr, v = f.pop(t, 2), f.pop(t, 2)
            if addr.const is not None and DIRECT_LO <= addr.const <= DIRECT_HI:
                self.direct.add(addr.const)
                f.bury_const(addr.const, v.expr)
            elif f.loops:
                f.open(f"if ({f.guard(addr.expr)}) {{")
                f.spill(); f.emit(f"rt_bury({addr.expr}, {v.expr});"); f.reload()
                f.close(f"}} else rt_bury({addr.expr}, {v.expr});")
            else:
                f.emit(f"rt_bury({addr.expr}, {v.expr});")
        elif t == "dig":
            addr = f.pop(t, 1)
            if addr.const is not None and DIRECT_LO <= addr.const <= DIRECT_HI:
                self.direct.add(addr.const)
                f.push(f.dig_const(addr.const))
            elif f.loops:
                f.nt += 1
                name = f"t{f.nt}"
                f.emit(f"V {name};")
                f.open(f"if ({f.guard(addr.expr)}) {{")
                f.spill(); f.emit(f"{name} = rt_dig({addr.expr});")
                f.close(f"}} else {name} = rt_dig({addr.expr});")
                f.push(Item(name))
            else:
                f.push(f.temp(f"rt_dig({addr.expr})"))
        elif t == "drain":
            f.emit("rt_drain();"); f.reload()
        elif t in ("digsum", "altsum"):
            b, x = f.pop(t, 2), f.pop(t, 2)
            f.push(f.temp(f"v_digits({x.expr}, {b.expr}, {1 if t == 'altsum' else 0})"))
        elif t == "prime":
            f.push(f.temp(f"v_prime({f.pop(t, 1).expr})"))
        elif t == "spf":
            f.push(f.temp(f"v_spf({f.pop(t, 1).expr})"))
        elif t == "factorings":
            f.push(f.temp(f"v_factorings({f.pop(t, 1).expr})"))
        elif t == "power":
            k, a = f.pop(t, 2), f.pop(t, 2)
            f.push(f.temp(f"v_power({a.expr}, {k.expr})"))
        elif t == "echo":
            hi, lo = f.pop(t, 2), f.pop(t, 2)
            f.spill()
            f.push(f.temp(f"rt_echo({lo.expr}, {hi.expr})"))
        elif t == "hush":
            f.emit("rt_hush();")
        elif t == "wave":
            hi, lo = f.pop(t, 2), f.pop(t, 2)
            f.push(f.temp(f"rt_wave({lo.expr}, {hi.expr})"))
        elif t == "want":
            f.emit(f"if ({f.pop(t, 1).expr} == 0) rt_sink(0, 0);")
        elif t == "more":
            f.emit("rt_sink(1, 0);")
        elif t == "aim":
            f.emit(f"rt_sink(2, {f.pop(t, 1).expr});")
        elif t == "worlds":
            f.push(f.temp("MKV(sunk)"))
        else:
            # a word defined nowhere: the run stops when it gets here, after printing what came before
            f.flush()
            f.emit(self.die_unknown(node))

    def pearl_scan(self, nodes):
        for n in nodes:
            if isinstance(n, PearlDecl):
                self.pearl_names.add(n.name)
            elif isinstance(n, Sweep):
                self.sweep_names.add(n.name)
            for sub in (getattr(n, "body", None), getattr(n, "then", None), getattr(n, "other", None)):
                if isinstance(sub, list):
                    self.pearl_scan(sub)

    def render(self, lines):
        out = []
        for ln in lines:
            if isinstance(ln, str):
                out.append(ln)
                continue
            kind, L, pad = ln
            cells, written = self.loop_cells[L], self.loop_written[L]
            if kind == "pre":
                out += [f"{pad}V {nm} = bed[{c} + BED_OFF];" for c, nm in sorted(cells.items())]
            elif kind in ("post", "spill"):
                out += [f"{pad}bed[{c} + BED_OFF] = {cells[c]};" for c in sorted(written)]
            elif kind == "reload":
                out += [f"{pad}{nm} = bed[{c} + BED_OFF];" for c, nm in sorted(cells.items())]
        glo, ghi = (min(self.promoted), max(self.promoted)) if self.promoted else (0, 0)
        return [ln.replace("@@GLO@@", str(glo)).replace("@@GHI@@", str(ghi)) for ln in out]

    def generate(self):
        from .interp import uses_worlds
        self.worlds = uses_worlds(self.prog)  # the same test as the interpreter: a world word anywhere
        self.pearl_names, self.sweep_names, self.sweep_callers = set(), set(), {}
        self.pearl_scan(self.prog.body)
        for d in self.prog.defs:
            self.pearl_scan(d.body)
        for name in sorted(self.pearl_names):
            self.pearl_ids.setdefault(name, len(self.pearl_ids))
        self.plan_inlining()
        reach = self.reachable()
        funcs = []
        for i, d in enumerate(self.prog.defs):
            if i not in reach or self.inline[i]:
                continue  # never called (or always copied into its callers): no function needed
            f = Fn(self)
            f.emit("GC_POLL(sp);")
            f.emit("if (UNLIKELY(++rt_depth > RT_MAX_DEPTH)) rt_too_deep();")
            self.body(f, d.body, i + 1, False)
            f.flush()
            f.emit("rt_depth--;")
            f.emit("return sp;")
            funcs.append((self.fn_name(i), f.lines))
        self.def_seen = 0
        main = Fn(self)
        for name in sorted(self.by_name):
            main.emit(f"cur{self.by_name[name][0]} = -1;")  # every world starts before any definition
        self.body(main, self.prog.body, 0, True)
        main.flush()
        main.emit("return sp;")
        lo = min(self.direct, default=0)
        hi = max(self.direct, default=0)
        off = max(4096, -lo + 1)
        out = [f"#define BED_OFF {off}",
               f"#define BED_INIT {max(65536, off + hi + 1)}",
               f"#define N_PEARL_WORDS {max(1, len(self.pearl_ids))}",
               '#include "trawl_rt.h"',
               f"static V K[{max(1, len(self.consts))}];"]
        out += [f"static int cur{self.by_name[name][0]} = -1;" for name in sorted(self.by_name)]
        out += [f"static long {name}(long sp);" for name, _ in funcs]
        for _, lines in self.sweep_callers.values():
            out += lines
        for name, lines in funcs:
            out.append(f"static long {name}(long sp) {{")
            out += self.render(lines)
            out.append("}")
        out.append("static void trawl_init_consts(void) {")
        out.append(f"  rt_world_mode = {1 if self.worlds else 0};")
        for i, n in enumerate(self.consts):
            out.append(f'  K[{i}] = v_from_str("{n}");')
        out.append("}")
        out.append("static long trawl_main(long sp) {")
        out += self.render(main.lines)
        out.append("}")
        code = "\n".join(out) + "\n"
        stamp = hashlib.sha256(code.encode()).hexdigest()[:16]  # a save file only resumes the program that wrote it
        return (f"/* made by the Trawl compiler from {os.path.basename(self.file)}. edit the .trawl file, not this one. */\n"
                f'#define TRAWL_PROG_HASH "{stamp}"\n' + code)


def to_c(prog, file="<program>"):
    return Gen(prog, file).generate()


def find_cc():
    for cc in (os.environ.get("CC"), "gcc", "clang", "cc"):
        if cc and shutil.which(cc):
            return cc
    raise TrawlError("no C compiler found. install gcc or clang (on Windows: w64devkit or MSYS2), or set CC")


def build(prog, out_path, file="<program>", opt="-O2", keep_c=None):
    """Compile a parsed program to a native executable at out_path."""
    code = to_c(prog, file)
    cc = find_cc()
    with tempfile.TemporaryDirectory() as tmp:
        c_path = keep_c or os.path.join(tmp, "prog.c")
        with open(c_path, "w", encoding="utf-8") as fh:
            fh.write(code)
        cmd = [cc, opt, "-std=gnu99", "-w", "-I", RUNTIME, "-o", out_path, "-x", "c", c_path, "-x", "none"]  # C whatever its name
        if os.name == "nt":  # Windows gives a program 1 MB of stack unless asked: ask for 256 MB
            cmd.append("-Wl,--stack,268435456")
        elif sys.platform == "darwin":
            cmd.append("-Wl,-stack_size,0x10000000")
        else:  # elsewhere the program runs on a thread of its own with a big stack (see trawl_rt.h)
            cmd.append("-pthread")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise TrawlError("the C compiler refused the generated code (this is a Trawl bug, please report it):\n" + r.stderr[-3000:])
    return out_path
