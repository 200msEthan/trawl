# Contributing to Trawl

Thanks for wanting to help. Small, careful changes are the most welcome kind.

## Ways to help

- **Engines that disagree.** A program where `trawl run` and `trawl fast` print different things is a bug. Open
  an issue with the program and both outputs.
- **Confusing errors.** A `~` message that doesn't say what went wrong in plain words.
- **Good first issues.** The ones labeled
  [good first issue](https://github.com/connabyss/trawl/labels/good%20first%20issue) say where to look.
- **New words or library pieces.** Please open an issue first, so we can talk before you build it.

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
| `tests/programs/` | old Tide programs and the exact output each must print |
| `playground/` | the browser playground. `playground/build.py` builds `index.html` from the interpreter |
| `tests/playground/` | browser tests for the playground |

## The playground

`playground/index.html` embeds the interpreter, the libraries and the examples. After you change any of them, run
`python playground/build.py` and commit `index.html` too. A test fails when it's out of date. Merging into
`main` publishes the playground.

Changed the page itself? Run its browser tests (once: `cd tests/playground && npm install && npx playwright install chromium`),
then `node tests/playground/state.test.js`.

## Pull requests

- One change per pull request. A short title in the [Conventional Commit](https://www.conventionalcommits.org/)
  style, like `fix(cgen): match the interpreter on negative modulo`.
- Say what was wrong, what you changed and how you checked it.
- CI runs `python -m pytest --all` on Linux, macOS and Windows, plus the playground checks. All must pass.
- AI helped? Welcome. Add an `Assisted-by: <tool>` line to the commit or the pull request, and make sure you've
  read and run the change yourself. Coding agents: read [AGENTS.md](AGENTS.md) too.
