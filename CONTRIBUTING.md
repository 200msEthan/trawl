# Contributing to Trawl

Thanks for wanting to help. Small, careful changes are the most welcome kind.

## Set up

```
git clone https://github.com/connabyss/trawl && cd trawl
pip install -e .[test]
python -m pytest          # the quick run, about 30 seconds
python -m pytest --all    # everything, as CI runs it: a few minutes
```

Run `--all` before you open a pull request.

You need Python 3.10 or newer and gcc or clang (the native half of the tests compiles C).

## The rules that keep Trawl honest

- **The interpreter is the definition.** `trawl/interp.py` and [docs/SPEC.md](docs/SPEC.md) say
  what a program means. If `trawl fast` prints something different, the compiler has the bug.
- **Both engines, same output.** A new word goes into the interpreter, the compiler
  (`trawl/cgen.py` plus `trawl/runtime/`), the spec, and a test that runs it on both.
- **Old programs keep working.** Every program in `tests/programs/` must keep printing exactly
  its `.out` file. Don't edit the `.out` files to make a test pass.
- **A fix comes with a test that fails without it.**
- **Errors are part of the language.** Messages start with `~`, say what went wrong in plain
  words, and are the same in both engines.

## Where things are

| | |
|---|---|
| `trawl/lang.py` | reading a program: tokens, `use`, parsing, error messages |
| `trawl/interp.py` | the reference interpreter |
| `trawl/cgen.py` | the compiler to C |
| `trawl/runtime/` | the C runtime: numbers, big integers, seabed, echo, worlds |
| `trawl/lib/` | the standard library, written in Trawl |
| `tests/test_trawl.py` | the test suite |

## Pull requests

One change per pull request, with a short description of what it does and how you checked it.
CI runs the tests on Linux, macOS and Windows; all must pass.
