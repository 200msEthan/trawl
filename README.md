# Trawl

**A small language for hunting patterns in the integers.** Iterate a rule, watch where it
goes, find the loops, search for the first number with a property. Exact whole numbers of
any size, digit sums in any base, loops that prove when they are stuck, a word that hears
when the state repeats, and guess-and-rewind search. A native compiler (through C) makes
the searches fast.

```
# rivers: x -> x + (digit sum of x)^2
: flow dup 10 digsum dup * + ;
1 15 [ swap dup . flow swap 1 - ] drop drop
```
```
1 2 6 42 78 303 339 564 789 1365 1590 1815 2040 2076 2301
```

Trawl grew out of **Tide**, a language two research Claudes made up overnight while
studying integer maps. A search written in Trawl reproduces
[two new terms of OEIS A343507](#the-proof-it-works). Every Tide program runs unchanged.

## Install

You need Python 3.9 or newer, and gcc or clang for the fast native builds
(on Windows: [w64devkit](https://github.com/skeeto/w64devkit) or MSYS2).

```
pip install git+https://github.com/connabyss/trawl
trawl run examples/hello.trawl      # the careful reference interpreter
trawl fast examples/hello.trawl     # compiled to native code, then run
trawl build examples/a343507.trawl  # writes a native program you can keep
trawl                               # an interactive sea (REPL)
```

## A quick tour

Numbers go on a stack; words take from it and give back.

```
2 3 + 4 * .            # 20
: sq dup * ;  12 sq .  # 144
2 100 power .          # 1267650600228229401496703205376 (never overflows)
1234 10 digsum .       # 10, the digit sum in base 10
1234 10 altsum .       # 2,  units - tens + hundreds - ...
42 7 bury  7 dig .     # 42: the seabed is memory with numbered cells
5 [ dup . 1 - ] drop   # 5 4 3 2 1: a loop runs while the top is nonzero
```

The words that make Trawl what it is:

| | |
|---|---|
| **stuck loops** | `trawl run` stops any loop that comes back to exactly the same state, with proof that it would never end, instead of hanging |
| `echo` | `lo hi echo` asks: have seabed cells lo..hi looked exactly like this before? It gives how many looks ago (the loop length), or 0 |
| `wave` `want` `more` `aim` | `1 9 wave` guesses a number; `want` rewinds to try the next guess when a condition fails; `more` collects every answer; `aim` keeps the best |
| `pearl` | `pearl fib` makes a word remember its answers |
| `digsum` `altsum` | digit sums in any base |

```
: step dup * 31 % ;    # the rule x -> x^2 mod 31
use orbits
3 orbit . .            # 4 1: falls into a loop of length 4 after 1 step
```

The full definition is in [docs/SPEC.md](docs/SPEC.md), the library in
[docs/STDLIB.md](docs/STDLIB.md), and what Trawl borrows from Forth and others (and what
it adds) in [docs/PRIOR_ART.md](docs/PRIOR_ART.md).

## Two engines, one answer

- **`trawl run`** is the reference interpreter, in Python. It checks for stuck loops and has
  a step limit. It defines what Trawl means.
- **`trawl fast` / `trawl build`** compile to C and then to machine code. Numbers are
  62-bit machine words that turn into exact big numbers when they overflow, so the output
  is always the same as the interpreter's. Seabed cells at fixed addresses (the usual way to
  keep variables) live in CPU registers inside loops.

The test suite checks that both engines print exactly the same thing, and that all 160
Tide programs from the original research print exactly what the old Tide engine printed.

## The proof it works

[OEIS A343507](https://oeis.org/A343507) lists, for each m, the least n with
((n+m)!)^2 dividing (2n)! (Erdős problem 727). The published table stopped at m = 9.
A search in C by one of the research Claudes found two new terms; the same search written
in Trawl ([examples/a343507.trawl](examples/a343507.trawl)) reproduces them:

| m | least n | found by the Trawl search in |
|---|---|---|
| 10 | 19295451536 | 8 minutes |
| 11 | 91328826321 | 33 minutes |

These two terms are not yet in the OEIS. On a fixed slice of the search, Trawl runs about
3 times slower than the hand-written C, 8 times faster than plain Python, and 170 times
faster than the original Tide engine; the details are in [docs/BENCHMARK.md](docs/BENCHMARK.md).

## Tests

```
pip install -e .[test]
python -m pytest
```

## License

MIT. See [LICENSE](LICENSE).
