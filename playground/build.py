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

# the examples, easy first and Erdős last: (file in playground/examples, title, what it does, an idea to try)
EXAMPLES = [
    ("collatz", "Collatz records",
     "Halve even numbers, turn odd n into 3n + 1, and count the steps down to 1. This hunt catches every "
     "starting number that takes longer than all the smaller ones. Nobody has proved every number reaches 1.",
     "change 10000 to 30000 for more records."),
    ("hello", "A first look",
     "Numbers go on a stack and words use them up: 12 sq means 12 times 12. Numbers never overflow, and "
     "digits work in any base.",
     "change 100 to 1000 and see how big 2 to the 1000 is."),
    ("rivers", "Digit rivers",
     "Start at 1. Each stop adds the square of its digit sum: 1, 2, 6, 42, 78... Where does the river go?",
     "start somewhere else by changing the first 1."),
    ("zerofree", "Powers of 2 with no zero",
     "Which powers of 2 have no digit 0? The last known one is 2^86. This hunt checks every power up to 2^300.",
     "check further by changing 300 to 1000 (it takes longer)."),
    ("reef", "Echo: when does it repeat?",
     "A ring of 13 reef spots grows by a simple rule each year. The word echo notices when the whole reef "
     "looks exactly like it did before, which tells you how long its cycle is.",
     "change n 13 to another size. Open the full printout below to see the reef."),
    ("queens", "Worlds: six queens",
     "Place six queens on a 6 by 6 board so none attack each other. wave guesses a row, and want throws away "
     "every world where a queen is attacked. The board is in the full printout below.",
     "change n 6 to 8."),
    ("pearls", "Pearls: remembered answers",
     "slow-fib asks itself the same question millions of times. A pearled word remembers each answer, so "
     "Fibonacci number 90 comes back instantly.",
     "ask for 200 kind-fib."),
    ("orbit", "Whirlpools: x squared mod 31",
     "Keep squaring and taking the remainder mod 31. Every start falls into a loop. orbit tells you how long "
     "the lead-in and the loop are.",
     "change 31 to another number, like 101."),
    ("stuck", "A loop that is proven stuck",
     "This loop never changes anything. Instead of hanging forever, Trawl sees the exact same state come back "
     "and stops with a proof that it can never end.",
     "change 5 drop to 1 + so the loop changes something (then press Stop)."),
    ("a343507", "OEIS A343507 by brute force",
     "For each n, the least k where ((k+n)!)^2 divides (2k)!. This is the sequence behind Erdős problem 727. "
     "The playground finds a(2) by checking every k. The fast search that found new terms is in examples/.",
     "try 3 a . instead of 2 a . (it takes a while here)."),
    ("k4", "Erdős 727: a k = 4 family",
     "Erdős problem 727 is open. This tests one family of numbers, n = 8t^4 - 18t^2 + 6, and catches each t "
     "that works for k = 4. It is a lead, not a proof.",
     "change 131 to 300 to search further."),
]
EXAMPLES = [(i, t, (HERE / "examples" / f"{i}.trawl").read_text(encoding="utf-8"), a, y) for i, t, a, y in EXAMPLES]

page = (HERE / "template.html").read_text(encoding="utf-8")
def js(value):
    # JSON is valid JavaScript; "</" is escaped so no text can close the <script> early
    return json.dumps(value).replace("</", "<\\/")


page = page.replace("/*@@PY@@*/{}", js(PY))
page = page.replace("/*@@WORK@@*/{}", js(WORK))
page = page.replace("/*@@EXAMPLES@@*/[]", js([{"id": i, "title": t, "src": s, "about": a, "try": y} for i, t, s, a, y in EXAMPLES]))
page = page.replace("@@VERSION@@", re.search(r'__version__ = "([^"]+)"', PY["trawl/__init__.py"]).group(1))
target = HERE / "index.html"
if "--check" in sys.argv:  # used by the tests: is index.html up to date?
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    sys.exit(0 if current == page else "playground/index.html is out of date: run python playground/build.py")
target.write_text(page, encoding="utf-8", newline="\n")
print(f"wrote {target} ({len(page) // 1024} KB)")
