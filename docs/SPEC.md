# The Trawl language

Version 0.1. This page is the definition of Trawl. Where the two engines could ever
disagree, the reference interpreter (`trawl run`) is right, and the native compiler
(`trawl fast`, `trawl build`) has a bug.

Trawl began as Tide, a language two research Claudes made up while hunting patterns in
integer sequences. Every Tide program runs unchanged in Trawl and prints the same thing.

## 1. Reading a program

A program is text. It is split into **tokens** at whitespace. There are four kinds:

| Token | Looks like | Meaning |
|---|---|---|
| number | `42`, `-7`, `+3`, `1_000_000` | push that whole number |
| text | `." hello sea"` | print the text (leading spaces after `."` are dropped) |
| word | `dup`, `digsum`, `my-word` | do what the word says |
| structure | `:` `;` `[` `]` `if` `else` `then` `use` `pearl` | shape the program |

- `#` starts a comment that runs to the end of the line, **except** on a line that also
  contains `."` text. (This keeps `#` usable inside printed text. Put comments on their
  own lines when a line prints something.)
- Words are case-sensitive and can contain any characters except whitespace.
- Number literals can be any size.

## 2. Numbers

Every value is a whole number of unlimited size. There are no fractions, floats, strings
or booleans. Comparisons give `1` for true and `0` for false; `if` and loops treat any
nonzero number as true.

- `/` rounds toward minus infinity and `%` takes the sign of the divisor (so
  `-7 2 /` is `-4` and `-7 2 %` is `1`). For any `a` and nonzero `b`:
  `a = (a b /) * b + (a b %)`.
- Dividing by zero is an error.

## 3. The stack

Words take their inputs from the top of the stack and leave their results there. Stack
effects are written `( before -- after )`, top of the stack on the right.

| Word | Effect | |
|---|---|---|
| `dup` | `( a -- a a )` | |
| `drop` | `( a -- )` | |
| `swap` | `( a b -- b a )` | |
| `over` | `( a b -- a b a )` | |
| `rot` | `( a b c -- b c a )` | |
| `fold` | `( ... -- reversed ... )` | reverses the whole stack |
| `depth` | `( -- n )` | how many numbers are on the stack |

## 4. Arithmetic and comparison

| Word | Effect |
|---|---|
| `+ - *` | `( a b -- a+b )`, `( a b -- a-b )`, `( a b -- a*b )` |
| `/ %` | `( a b -- floor(a/b) )`, `( a b -- a mod b )` |
| `= < >` | `( a b -- flag )` |
| `power` | `( a k -- a^k )`, `k >= 0` (`0 0 power` is 1) |

## 5. Digits in any base

These are Trawl's own words, the ones the research ran on.

| Word | Effect | |
|---|---|---|
| `digsum` | `( x base -- s )` | sum of the digits of `abs(x)` in `base` (`base >= 2`). Always matches `x` mod `base - 1`. |
| `altsum` | `( x base -- s )` | units - tens + hundreds - ... of `abs(x)`. Always matches `x` mod `base + 1`. |

`1234 10 digsum` is 10. `1234 10 altsum` is 2. `255 2 digsum` is 8 (the number of 1 bits).

## 6. Printing

| Word | Effect | Prints |
|---|---|---|
| `.` | `( a -- )` | the number and a space |
| `emit` | `( c -- )` | the character with that code (UTF-8) |
| `." text"` | `( -- )` | the text |
| `.s` | `( -- )` | the stack, like `<1 2 3> ` |
| `.bed` | `( -- )` | the nonzero seabed cells, like `{7:42 9:1} ` |
| `.pearls` | `( -- )` | how many answers the pearls remember, like `(12 pearls) ` |

When a program finishes, a newline is printed.

## 7. Control

- `cond if A then` runs `A` when `cond` is nonzero. `cond if A else B then` picks one.
  `if` removes `cond`.
- `[ A ]` is a loop: **while the top of the stack is nonzero, run `A`**. The top is
  checked, not removed. An empty stack ends the loop. So the usual shapes are:

```
cond [ drop  A  cond ] drop        # while cond: A
1 [ drop  A  cond ] drop           # do A, repeat while cond
```

## 8. Words of your own

`: name body ;` defines a word. Words can call themselves and each other. A word can be
defined again later; from then on the new meaning is used. Built-in words can't be
redefined, and a number can't be a word's name.

```
: sq dup * ;
: fact dup 2 < if drop 1 else dup 1 - fact * then ;
```

`use name` reads `name.trawl` (or `name.tide`) right there, from the program's own folder
or from the standard library. See [STDLIB.md](STDLIB.md).

## 9. The seabed

The seabed is memory: numbered cells, every one starting at 0. Addresses are any whole
number (the native build needs them below 2^62 in size).

| Word | Effect | |
|---|---|---|
| `bury` | `( value addr -- )` | store `value` at `addr` |
| `dig` | `( addr -- value )` | read the cell (0 if never set) |
| `drain` | `( -- )` | set every cell back to 0 |

By habit, small negative addresses are used as named variables (`-7 dig`, `5 -7 bury`)
and positive ranges as arrays. The native compiler is built around that habit: cells at
fixed addresses inside loops are kept in CPU registers.

## 10. Echo: has this happened before?

| Word | Effect | |
|---|---|---|
| `echo` | `( lo hi -- k )` | look at seabed cells `lo..hi`. If they have looked exactly like this at an earlier `echo`, push how many echoes ago that was (the length of the loop); otherwise push 0. Either way, remember this picture. |
| `hush` | `( -- )` | forget every picture |

With `echo`, a program can find for itself when a system (a reef, a deck, a ferry) starts
repeating, and how long the repeat is.

## 11. Worlds: guess, and rewind when wrong

| Word | Effect | |
|---|---|---|
| `wave` | `( lo hi -- x )` | guess a number from `lo` to `hi` |
| `want` | `( flag -- )` | if `flag` is 0 this world is wrong: it sinks, and the sea rewinds to the last guess and tries its next number |
| `more` | `( -- )` | keep what this world printed, then sink it anyway to find the next world |
| `aim` | `( score -- )` | remember this world and its score, then sink it; at the end, what the lowest-scoring world printed is shown (ties go to the first) |
| `worlds` | `( -- n )` | how many worlds have sunk so far |

Each world replays the program from the start in a fresh sea (empty stack, empty seabed,
no echoes, no pearls), following the guesses so far. A program that uses these words
prints what its worlds kept, then a line `(N worlds sank along the way)`. If every world
sinks and none was kept or aimed, that is an error. After 2,000,000 sunk worlds the search
stops with an error.

```
: queen 1 4 wave ;   # ... want ... more     (see examples/queens.trawl)
```

## 12. Pearls: remembered answers

`pearl name` makes the word `name` remember answers. A pearled word must take one number
and leave one; after `pearl name`, asking it the same number again gives the remembered
answer without running it. Pearls work through recursion, so
`: fib dup 2 < if else dup 1 - fib swap 2 - fib + then ; pearl fib` makes `90 fib` instant.

## 13. Errors

Errors stop the program and print one line starting with `~`. The wording is part of the
language and the same in both engines, for example:

- `the tide went out too far: `+` wanted 2 numbers, the shore was empty`
- `you tried to split 7 into zero pieces. the ocean refuses.`
- `base 1 has no digits. try 2 or more.`
- `tide numbers are whole: 2 to the power -1 would be a fraction`
- `nobody in the sea knows the word `flod`. did you mean `fold`?`
- `the tide is stuck: this loop came back to the exact same sea [...], so it can never end` (interpreter only, see below)

## 14. The two engines

**`trawl run`** (reference interpreter, Python) checks two extra things:

- **Stuck loops.** Trawl is deterministic, so a loop that comes back to the exact same stack
  and seabed will repeat forever. The interpreter remembers what the sea looked like at the
  top of each pass (while stack plus seabed hold at most 256 numbers) and stops with a
  proof when a picture repeats.
- **A step limit** (50 million by default; `--max-steps N`).

**`trawl fast` / `trawl build`** (native compiler, through C) skip both checks, for speed.
They also differ in a few edges that don't change any correct program's output:

- Output is printed as the program runs (the interpreter in worlds mode collects it).
- Unknown words are reported before the program starts, not when they are reached.
- Definitions are resolved where a word is used in the text. A word used at the top level
  before any definition of it is an error when reached, as in the interpreter.
- Seabed addresses must be smaller than 2^62 in size.

Everything else (results, printed text, error messages) is the same, and the test suite
checks that on every example and every old Tide program.
