# CLAUDE.md

Trawl is a small stack language for hunting patterns in the integers: a reference
interpreter in Python (`trawl run`) and a native compiler through C (`trawl fast`, `trawl build`).

- Spec: docs/SPEC.md. The interpreter defines the language; if the engines disagree, the compiler has the bug.
- Tests: `pip install -e .[test]` then `python -m pytest` for the quick run (about 30 s), or `python -m pytest --all` for everything, as CI does (needs gcc or clang for the native half).
  Every old Tide program in tests/programs must keep printing exactly its .out file.
- A fix gets a test that fails without it.

## Rules (from connabyss)

- Commit as `CH <340279802+connabyss@users.noreply.github.com>`, never a personal email.
- Fewer PRs with more changes each. Don't merge: open the PR, assign connabyss, let them merge.
- Nothing goes public (repo visibility, releases, posts) without connabyss's OK.
