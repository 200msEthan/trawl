<!-- Thanks for the PR! Check what applies, or write n/a on a line that doesn't. -->

## What this changes

<!-- One or two sentences. Link the issue it fixes. -->

## How it was checked

<!-- The commands you ran and what they printed. -->

## Checklist

- [ ] The title is a short Conventional Commit, for example `fix(cgen): match the interpreter on negative modulo`.
- [ ] A fix has a test that fails when the change is taken out.
- [ ] `python -m pytest --all` passes.
- [ ] A new or changed word works the same in `trawl run` and `trawl fast`, and the spec says so.
- [ ] No `.out` file in `tests/programs/` was edited to make a test pass.
- [ ] Changed the interpreter, a library or an example: I ran `python playground/build.py` and committed `playground/index.html`.

Nice to have: an `Assisted-by: <tool>` line if an AI helped.
