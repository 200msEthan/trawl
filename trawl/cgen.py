"""The native compiler: Trawl -> C -> machine code.

Each Trawl word becomes one C function `long w(long sp)` that works on a shared stack.
Inside a straight run of words the compiler keeps the top of the stack in C variables
instead of memory, so `dup * 1 +` turns into a few register operations. Numbers are
62-bit machine words that switch to exact big numbers on overflow (trawl_rt.h), so the
native build prints exactly what the reference interpreter prints.
"""
import os
import re
import shutil
import subprocess
import tempfile

from .lang import (Def, If, Loop, Num, PearlDecl, Program, Text, TrawlError, WANTS, Word,
                   BUILTINS, suggest, where)

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
        self.pearl_ids = {}
        self.worlds = False
        self.def_seen = 0
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

    # -- word lookup (the definition in force where the word is used)
    def resolve(self, name, at, top_level):
        idxs = self.by_name.get(name)
        if not idxs:
            return None
        bodies = {self.shape(self.prog.defs[i].body) for i in idxs}
        if len(bodies) == 1:
            return idxs[-1]
        before = [i for i in idxs if i < at]
        if before:
            return before[-1]
        return None if top_level else idxs[0]

    def shape(self, body):
        out = []
        for n in body:
            if isinstance(n, Word):
                out.append(n.name)
            elif isinstance(n, Num):
                out.append(str(n.value))
            elif isinstance(n, Text):
                out.append("." + n.text)
            elif isinstance(n, Loop):
                out.append("[" + self.shape(n.body) + "]")
            elif isinstance(n, If):
                out.append("if(" + self.shape(n.then) + "|" + (self.shape(n.other) if n.other is not None else "") + ")")
            elif isinstance(n, Def):
                out.append(":" + n.name + "(" + self.shape(n.body) + ")")
            elif isinstance(n, PearlDecl):
                out.append("pearl " + n.name)
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
                if isinstance(node, Word):
                    j = self.resolve(node.name, i + 1, False)
                    if j is not None:
                        calls[i].add(j)
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
                if self.inline[i] or not plain[i] or self.prog.defs[i].name in self.pearl_names or reaches(i):
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
                elif isinstance(n, Word):
                    j = self.resolve(n.name, at, top)
                    if j is not None and j not in seen:
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
                f.emit(f"rt_puts({c_string(node.text)});")
            elif k is Def:
                if top_level:
                    self.def_seen += 1
            elif k is PearlDecl:
                pid = self.pearl_ids.setdefault(node.name, len(self.pearl_ids))
                f.emit(f"shells[{pid}].on = 1;")
            elif k is If:
                self.if_(f, node, at, top_level)
            elif k is Loop:
                self.loop(f, node, at, top_level)
            elif k is Word:
                self.word(f, node, self.at(at, top_level), top_level)

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

    def word(self, f, node, at, top_level):
        t = node.name
        target = self.resolve(t, at, top_level)
        if target is None and t in self.by_name:
            # used at the top level before any definition: the sea doesn't know it yet
            f.flush()
            msg = f"{where(node.tok)}: nobody in the sea knows the word `{t}` yet (it is defined further down)"
            f.emit(f"rt_die(\"%s\", {c_string(msg)});")
            return
        if target is not None:
            if self.inline[target]:
                self.body(f, self.prog.defs[target].body, target + 1, False)
            else:
                self.call(f, node, target)
            return
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
            self.worlds = True
            hi, lo = f.pop(t, 2), f.pop(t, 2)
            f.push(f.temp(f"rt_wave({lo.expr}, {hi.expr})"))
        elif t == "want":
            self.worlds = True
            f.emit(f"if ({f.pop(t, 1).expr} == 0) rt_sink(0, 0);")
        elif t == "more":
            self.worlds = True
            f.emit("rt_sink(1, 0);")
        elif t == "aim":
            self.worlds = True
            f.emit(f"rt_sink(2, {f.pop(t, 1).expr});")
        elif t == "worlds":
            f.push(f.temp("MKV(sunk)"))
        else:
            raise TrawlError(f"{where(node.tok)}: nobody in the sea knows the word `{t}`.{suggest(t, self.by_name)}")

    def pearl_scan(self, nodes):
        for n in nodes:
            if isinstance(n, PearlDecl):
                self.pearl_names.add(n.name)
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
        self.pearl_names = set()
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
            self.body(f, d.body, i + 1, False)
            f.flush()
            f.emit("return sp;")
            funcs.append((self.fn_name(i), f.lines))
        self.def_seen = 0
        main = Fn(self)
        self.body(main, self.prog.body, 0, True)
        main.flush()
        main.emit("return sp;")
        lo = min(self.direct, default=0)
        hi = max(self.direct, default=0)
        off = max(4096, -lo + 1)
        out = [f"/* made by the Trawl compiler from {os.path.basename(self.file)}. edit the .trawl file, not this one. */",
               f"#define BED_OFF {off}",
               f"#define BED_INIT {max(65536, off + hi + 1)}",
               f"#define N_PEARL_WORDS {max(1, len(self.pearl_ids))}",
               '#include "trawl_rt.h"',
               f"static V K[{max(1, len(self.consts))}];"]
        out += [f"static long {name}(long sp);" for name, _ in funcs]
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
        return "\n".join(out) + "\n"


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
        cmd = [cc, opt, "-std=gnu99", "-w", "-I", RUNTIME, "-o", out_path, c_path]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise TrawlError("the C compiler refused the generated code (this is a Trawl bug, please report it):\n" + r.stderr[-3000:])
    return out_path
