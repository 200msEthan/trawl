# Where Trawl comes from, and what is new

Trawl is not a copy of another language. But almost no single idea in any language is new,
and Trawl is no exception, so here is an honest account of what it borrows and what it adds.

## The name

The language was first called Tide. That name was already used by another small
programming language on GitHub and by a Rust web framework, so it was renamed Trawl
(dragging a net through the integers). We found no programming language called Trawl.

## Borrowed, and proud of it

| Trawl | Comes from |
|---|---|
| A stack of numbers; words that take from it and give back; postfix (`2 3 +`) | **Forth** (Charles H. Moore, around 1970), and the concatenative family after it (Joy, Factor) |
| `dup drop swap over rot`, `: name ... ;`, `.`, `emit`, `.s`, `if else then` | **Forth**, the same names |
| `[ ... ]` loops while the top is nonzero | the `[ ]` loop of **Brainfuck** (Urban Müller, 1993), which loops while the current cell is nonzero |
| Whole numbers of unlimited size | Lisp, Python, PARI/GP, Mathematica and many more |
| `bury` / `dig` memory | Forth's `!` and `@`; Trawl's seabed is sparse (any address, unset cells are 0) |
| `digsum` (digit sum in any base) | PARI/GP has `sumdigits(n, b)` |
| `wave` / `want` (guess, and rewind when wrong) | McCarthy's `amb` (1963), Prolog's backtracking, Icon's goal-directed evaluation |
| `pearl` (remember a word's answers) | memoization, a standard technique |
| Finding when a sequence repeats | Floyd's and Brent's cycle-finding methods; Mathematica's `FindTransientRepeat` works on lists |

## What Trawl adds

What we did not find elsewhere is the combination, aimed at one job: **running experiments
on integer sequences and maps** (iterate a rule, watch where it goes, find loops, search for
the first number with a property).

1. **Loops that prove they are stuck.** Trawl is deterministic, so the reference interpreter
   checks every loop pass against the earlier ones; if the stack and seabed come back
   exactly, the loop can never end, and the program stops with that proof instead of hanging.
   We did not find another language that does this as part of what a loop means.
2. **`echo` as a basic word.** One word answers "has this part of memory looked exactly like
   this before, and how many looks ago?", which is the loop length of whatever system the
   program is simulating. In other languages this is a library routine you write yourself.
3. **Worlds with a built-in "best".** `wave`/`want`/`more` are classic backtracking, but
   `aim` (keep the lowest-scoring world) makes small optimization searches one word long,
   and each world replays from a clean sea, so the seabed and echo just work inside a search.
4. **Digit arithmetic in any base as core words.** `digsum` and `altsum` (the alternating
   digit sum, which matches x mod (base+1)) are part of the language, because the research
   that Trawl grew from ran on them.
5. **A native compiler built around how these programs are written.** Research programs keep
   their variables in seabed cells at fixed addresses (`-7 dig`). The compiler keeps those
   cells in CPU registers inside loops, while numbers still switch to exact big integers
   on overflow, so the fast build prints exactly what the interpreter prints.

## How we checked

Web searches (November 2026) for the name, for stack languages aimed at number theory, and for
languages with built-in cycle detection or backtracking. This is a search, not a proof:
if you know of a language that already does items 1 to 3 together, please open an issue.
