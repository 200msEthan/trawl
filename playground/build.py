"""Build playground/index.html: one self-contained page that runs the Trawl reference
interpreter in the browser (Python compiled to WebAssembly by Pyodide).

    python playground/build.py          (python playground/build.py --check: is it up to date?)

Run it again whenever the interpreter, the libraries or the examples change.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERE = ROOT / "playground"

# the interpreter: pure Python, no compiler needed
PY = {f"trawl/{n}": (ROOT / "trawl" / n).read_text(encoding="utf-8")
      for n in ("__init__.py", "lang.py", "interp.py")}
for f in sorted((ROOT / "trawl" / "lib").glob("*.trawl")):
    PY[f"trawl/lib/{f.name}"] = f.read_text(encoding="utf-8")

# libraries the examples `use`, placed next to the program
WORK = {}
for f in [*sorted((ROOT / "examples" / "erdos727").glob("*.trawl"))]:
    if not f.name.startswith("run_"):
        WORK[f.name] = f.read_text(encoding="utf-8")

EXAMPLES = [
    ("hello", "A first look", (ROOT / "examples" / "hello.trawl").read_text(encoding="utf-8")),
    ("rivers", "Digit rivers", "# rivers: x -> x + (digit sum of x)^2\n: flow dup 10 digsum dup * + ;\n1 15 [ swap dup . flow swap 1 - ] drop drop\n"),
    ("reef", "Echo: when does it repeat?", (ROOT / "examples" / "reef.trawl").read_text(encoding="utf-8")),
    ("whirlpools", "Whirlpools", (ROOT / "examples" / "whirlpools.trawl").read_text(encoding="utf-8")),
    ("queens", "Worlds: six queens", (ROOT / "examples" / "queens.trawl").read_text(encoding="utf-8")),
    ("pearls", "Pearls: remembered answers", (ROOT / "examples" / "pearls.trawl").read_text(encoding="utf-8")),
    ("orbit", "Orbits: x^2 mod 31", ": step dup * 31 % ;    # the rule x -> x^2 mod 31\nuse orbits\n3 orbit . .            # loop length, then steps before the loop\n"),
    ("a343507", "OEIS A343507 by brute force", "# a(n) = the least k with ((k+n)!)^2 dividing (2k)!   (OEIS A343507, Erd\u0151s problem 727)\n# fits: for every prime p up to k+n, the power of p in (2k)! must be at least\n# twice its power in (k+n)!. Cells: -2 k+n, -3 2k, -4 verdict, -6 p.\nuse numbers\n: fits\n  over + -2 bury  2 * -3 bury  1 -4 bury  2 -6 bury\n  1 [ drop\n    -6 dig prime if\n      -3 dig -6 dig legendre  -2 dig -6 dig legendre 2 *  < if 0 -4 bury then\n    then\n    -6 dig 1 + -6 bury\n    -6 dig -2 dig > 0 =  -4 dig * ] drop\n  -4 dig ;\n# a: try k = 0, 1, 2, ... until one fits\n: a -5 bury 0 1 [ drop dup -5 dig fits if 0 else 1 + 1 then ] drop ;\n2 a .        # 208.  Try 3 a . too (it takes several seconds here).\n# The fast search that found a(10) and a(11) is examples/a343507.trawl, run with trawl fast.\n"),
    ("k4", "Erdős 727: a k = 4 family", "# n = 8t^4 - 18t^2 + 6 makes n+1..n+4 split into small factors.\n# Which t give ((n+4)!)^2 | (2n)! ?  A lead, not a proof: see examples/erdos727.\nuse quartic_k4\n60 130 hunt\n"),
    ("stuck", "A loop that is proven stuck", "# this loop never changes anything, so it can never end.\n# Trawl notices the exact same state coming back and stops with a proof.\n1 [ 5 drop ]\n"),
]

page = (HERE / "template.html").read_text(encoding="utf-8")
def js(value):
    # JSON is valid JavaScript; "</" is escaped so no text can close the <script> early
    return json.dumps(value).replace("</", "<\\/")


page = page.replace("/*@@PY@@*/{}", js(PY))
page = page.replace("/*@@WORK@@*/{}", js(WORK))
page = page.replace("/*@@EXAMPLES@@*/[]", js([{"id": i, "title": t, "src": s} for i, t, s in EXAMPLES]))
page = page.replace("@@VERSION@@", re.search(r'__version__ = "([^"]+)"', PY["trawl/__init__.py"]).group(1))
target = HERE / "index.html"
if "--check" in sys.argv:  # used by the tests: is index.html up to date?
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    sys.exit(0 if current == page else "playground/index.html is out of date: run python playground/build.py")
target.write_text(page, encoding="utf-8", newline="\n")
print(f"wrote {target} ({len(page) // 1024} KB)")
