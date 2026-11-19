# Notes for coding agents

Read [CONTRIBUTING.md](CONTRIBUTING.md) first. It applies to you too.

Trawl is a small stack language for hunting patterns in the integers: a reference interpreter in Python
(`trawl run`) and a native compiler through C (`trawl fast`, `trawl build`). The spec is
[docs/SPEC.md](docs/SPEC.md).

- Set up with `pip install -e .[test]`. Run `python -m pytest` after every change (about two minutes), and
  `python -m pytest --all` before you open a pull request. `--all` needs gcc or clang.
- The interpreter defines the language. If the engines disagree, the compiler has the bug. Never change
  `trawl/interp.py` to match `trawl/cgen.py`.
- A new word goes into the interpreter, the compiler (`trawl/cgen.py` and `trawl/runtime/`), the spec, and a test
  that runs it on both engines.
- Never edit a `.out` file in `tests/programs/` to make a test pass. Every old Tide program must keep printing
  exactly its `.out` file.
- A fix gets a test that fails without it. Take your change out, run the test, see it fail.
- Errors start with `~`, say what went wrong in plain words, and match in both engines.
- Changed the interpreter, a library or an example? Run `python playground/build.py` and commit
  `playground/index.html` too. Don't edit `index.html` by hand; change `playground/template.html` or `build.py`.
- CI runs on Linux, macOS and Windows. Use `pathlib` for paths and keep the C runtime portable (gcc, clang and
  Windows).
- Write the pull request title as a short [Conventional Commit](https://www.conventionalcommits.org/), and add an
  `Assisted-by: <tool>` line.
