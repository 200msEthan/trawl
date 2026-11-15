"""The `trawl` command.

  trawl run  prog.trawl     run with the reference interpreter (checks for stuck loops)
  trawl fast prog.trawl     build natively and run (the speed you want for real searches)
  trawl build prog.trawl    write a native program next to the source (prog, or prog.exe)
  trawl c    prog.trawl     print the generated C
  trawl repl                type Trawl and see the stack after each line
"""
import argparse
import os
import subprocess
import sys
import tempfile

from . import __version__
from .lang import TrawlError, parse


def load(path):
    with open(path, encoding="utf-8") as fh:
        return parse(fh.read(), path)


def exe_name(path):
    base = os.path.splitext(path)[0]
    return base + (".exe" if os.name == "nt" else "")


def cmd_run(args):
    from .interp import run
    prog = load(args.file)
    out, sunk = run(prog, max_steps=args.max_steps, stream=None if sunk_mode(prog) else sys.stdout)
    if sunk is None:
        sys.stdout.write(out + "\n")
    else:
        print(out)
        print(f"({sunk} worlds sank along the way)")


def sunk_mode(prog):
    from .interp import uses_worlds
    return uses_worlds(prog)


def cmd_build(args):
    from .cgen import build
    prog = load(args.file)
    out = args.output or exe_name(args.file)
    build(prog, out, args.file, keep_c=args.keep_c)
    print(f"built {out}")


def cmd_fast(args):
    from .cgen import build
    prog = load(args.file)
    with tempfile.TemporaryDirectory() as tmp:
        exe = os.path.join(tmp, "prog" + (".exe" if os.name == "nt" else ""))
        build(prog, exe, args.file)
        sys.stdout.flush()
        sys.exit(subprocess.call([exe]))


def cmd_c(args):
    from .cgen import to_c
    sys.stdout.write(to_c(load(args.file), args.file))


def cmd_repl(args):
    from .interp import Sea
    sea, n = Sea(), 0
    print(f"Trawl {__version__}. the sea is calm. try: use stdlib 10 fib .   (ctrl+d to leave)")
    while True:
        try:
            line = input(f"trawl[{n}]> ")
        except (EOFError, KeyboardInterrupt):
            print("\nthe tide goes out. bye.")
            return
        n += 1
        sea.out, saved, sea.steps = [], list(sea.stack), 0
        try:
            sea.run(parse(line))
            print("".join(sea.out) + ("  " if sea.out else "") + "\033[2m" + str(sea.stack) + "\033[0m")
        except TrawlError as e:
            sea.stack = saved
            print(f"\033[1;91m~ {e}\033[0m  \033[2m(stack put back: {saved})\033[0m")


def main(argv=None):
    p = argparse.ArgumentParser(prog="trawl", description="Trawl: a language for hunting patterns in the integers.")
    p.add_argument("--version", action="version", version=f"trawl {__version__}")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run with the reference interpreter")
    r.add_argument("file"); r.add_argument("--max-steps", type=int, default=50_000_000)
    r.set_defaults(fn=cmd_run)
    f = sub.add_parser("fast", help="build natively and run")
    f.add_argument("file"); f.set_defaults(fn=cmd_fast)
    b = sub.add_parser("build", help="build a native program")
    b.add_argument("file"); b.add_argument("-o", "--output"); b.add_argument("--keep-c")
    b.set_defaults(fn=cmd_build)
    c = sub.add_parser("c", help="print the generated C")
    c.add_argument("file"); c.set_defaults(fn=cmd_c)
    q = sub.add_parser("repl", help="interactive sea")
    q.set_defaults(fn=cmd_repl)
    args = p.parse_args(argv)
    if not args.cmd:
        if sys.stdin.isatty():
            return cmd_repl(args)
        p.print_help()
        return
    try:
        args.fn(args)
    except TrawlError as e:
        sys.stdout.flush()
        sys.exit(f"~ {e}")
    except OSError as e:
        sys.exit(f"~ {e}")


if __name__ == "__main__":
    main()
