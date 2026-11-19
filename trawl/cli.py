"""The `trawl` command.

  trawl run  prog.trawl     run with the reference interpreter (checks for stuck loops)
  trawl fast prog.trawl     build natively and run (the speed you want for real searches)
                            --jobs N: sweeps on N cores; --save FILE: resume a long run
  trawl build prog.trawl    write a native program next to the source (prog, or prog.exe)
  trawl c    prog.trawl     print the generated C
  trawl verify run.receipt  re-run a receipt (--receipt FILE on run or fast) and check its output
  trawl repl                type Trawl and see the stack after each line
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

from . import __version__
from .lang import TrawlError, parse, read_source

if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(0)  # numbers of any size, also in the compiler's constants


def load(path):
    return parse(read_source(path), path)


def program_paths(source):
    """The program and every library it uses, so no output file can land on any of them."""
    used = []
    try:
        parse(read_source(source), source, used=used)
    except (TrawlError, OSError):
        pass  # the run itself will say what's wrong with it
    return [source] + used


def check_output(path, sources, flag):
    """Refuse an output file that would replace the program or a library it uses, or that has nowhere to go,
    before any work."""
    if path is None:
        return
    if path == "":
        raise TrawlError(f"`{flag}` needs a file name")
    if os.path.isdir(path):
        raise TrawlError(f"`{flag} {path}` is a folder. give a file name")
    for i, src in enumerate(sources):
        if os.path.exists(path) and os.path.exists(src) and os.path.samefile(path, src):
            what = "your program" if i == 0 else f"{os.path.basename(src)}, a library your program uses"
            raise TrawlError(f"`{flag} {path}` would write over {what}. pick another file name")
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        raise TrawlError(f"`{flag} {path}`: there is no folder {folder}")


def check_save(path):
    """--save may only replace a Trawl save file (or nothing): never notes, code or anything else."""
    if path and os.path.isfile(path) and os.path.getsize(path):
        with open(path, "rb") as fh:
            if not fh.read(11) == b"trawl-save ":
                raise TrawlError(f"`--save {path}`: that file isn't a Trawl save file, and a run would write over "
                                 "it. pick a new file name")


def check_steps(n):
    if n < 1:
        raise TrawlError(f"--max-steps needs a number of steps above 0, not {n}")


def exe_name(path):
    base = os.path.splitext(path)[0]
    return base + (".exe" if os.name == "nt" else "")


def cmd_run(args):
    from .interp import run
    from .progress import Ticker
    check_steps(args.max_steps)
    check_output(args.receipt, program_paths(args.file), "--receipt")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # the same bytes the native build writes, on any console
    if args.receipt:
        from .receipt import Tee, program_files, write_receipt
        prog, files, main = program_files(args.file)
        real, t0 = sys.stdout, time.time()
        tee = Tee(real)
        with Ticker(out=tee) as ticker:
            sys.stdout = ticker if ticker.enabled else tee
            try:
                _run_and_print(run, prog, args)
            finally:
                sys.stdout = real
        write_receipt(args.receipt, files=files, main=main, engine="run", output=tee.data(), seconds=time.time() - t0)
        ticker.done()
        print(f"~ receipt written to {args.receipt}", file=sys.stderr)
        return
    prog, real = load(args.file), sys.stdout
    with Ticker() as ticker:
        sys.stdout = ticker if ticker.enabled else real
        try:
            _run_and_print(run, prog, args)
        finally:
            sys.stdout = real
    ticker.done()


def _run_and_print(run, prog, args):
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
    if args.output == "":
        raise TrawlError("`-o` needs a file name")
    out = args.output or exe_name(args.file)
    paths = program_paths(args.file)
    check_output(out, paths, "-o")
    check_output(args.keep_c, paths, "--keep-c")
    if args.keep_c is not None and os.path.abspath(args.keep_c) == os.path.abspath(out):
        raise TrawlError("`-o` and `--keep-c` name the same file. give the C file its own name")
    prog = load(args.file)
    build(prog, out, args.file, keep_c=args.keep_c)
    print(f"built {out}")


def cmd_fast(args):
    from .cgen import build
    paths = program_paths(args.file)
    check_output(args.receipt, paths, "--receipt")
    check_output(args.save, paths, "--save")
    check_save(args.save)
    if args.receipt:
        from .receipt import program_files
        prog, files, main = program_files(args.file)
    else:
        prog = load(args.file)
    with tempfile.TemporaryDirectory() as tmp:
        exe = os.path.join(tmp, "prog" + (".exe" if os.name == "nt" else ""))
        build(prog, exe, args.file)
        sys.stdout.flush()
        extra = []
        if args.jobs is not None:
            extra += ["--jobs", str(args.jobs)]
        if args.save:
            extra += ["--save", os.path.abspath(args.save)]
        from .progress import Ticker
        from .receipt import run_native_teed, write_receipt
        ticker = Ticker()
        if not args.receipt:
            if not ticker.enabled:
                sys.exit(subprocess.call([exe] + extra))
            with ticker:
                code, _ = run_native_teed([exe] + extra, ticker=ticker, keep=False)
            if code == 0:
                ticker.done()
            sys.exit(code)
        t0 = time.time()
        with ticker:
            code, output = run_native_teed([exe] + extra, ticker=ticker if ticker.enabled else None)
        if code != 0:
            print("~ no receipt: the run stopped with an error", file=sys.stderr)
            sys.exit(code)
        write_receipt(args.receipt, files=files, main=main, engine="fast", output=output, seconds=time.time() - t0,
                      jobs=args.jobs, save=args.save)
        ticker.done()
        print(f"~ receipt written to {args.receipt}", file=sys.stderr)


def cmd_verify(args):
    from .receipt import verify
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # a console that can't show ✓ gets ? instead of a crash
    check_steps(args.max_steps)
    ok = verify(args.receipt, engine=args.engine, save=args.save, sample=args.sample, max_steps=args.max_steps,
                seed=args.seed)
    sys.exit(0 if ok else 1)


def cmd_c(args):
    from .cgen import to_c
    sys.stdout.write(to_c(load(args.file), args.file))


def cmd_repl(args):
    from .interp import TOO_DEEP, Sea, _deep
    sea, n = Sea(), 0
    color = sys.stdout.isatty()
    dim, red, off = ("\033[2m", "\033[1;91m", "\033[0m") if color else ("", "", "")
    print(f"Trawl {__version__}. the sea is calm. try: use stdlib 10 fib .   (ctrl+d to leave)")
    while True:
        try:
            line = input(f"trawl[{n}]> ")
        except (EOFError, KeyboardInterrupt):
            print("\nthe tide goes out. bye.")
            return
        n += 1
        sea.out, saved, sea.steps, limit = [], list(sea.stack), 0, sea.max_steps

        def stop():  # Ctrl+C: the line stops at its next step, and the sea keeps its words
            sea.max_steps = -1

        try:
            try:
                _deep(lambda: sea.run(parse(line)), on_interrupt=stop)
            except RecursionError:
                raise TrawlError(TOO_DEEP) from None
            print("".join(sea.out) + ("  " if sea.out else "") + dim + str(sea.stack) + off)
        except KeyboardInterrupt:
            sea.stack = saved
            print(f"{red}~ stopped (Ctrl+C){off}  {dim}(stack put back: {saved}){off}")
        except TrawlError as e:
            sea.stack = saved
            print(f"{red}~ {e}{off}  {dim}(stack put back: {saved}){off}")
        finally:
            sea.max_steps = limit


def main(argv=None):
    p = argparse.ArgumentParser(prog="trawl", description="Trawl: a language for hunting patterns in the integers.")
    p.add_argument("--version", action="version", version=f"trawl {__version__}")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run with the reference interpreter")
    r.add_argument("file"); r.add_argument("--max-steps", type=int, default=50_000_000)
    r.add_argument("--receipt", metavar="FILE", help="write a receipt of this run (check it with trawl verify)")
    r.set_defaults(fn=cmd_run)
    f = sub.add_parser("fast", help="build natively and run")
    f.add_argument("file")
    f.add_argument("-j", "--jobs", type=int, help="run each top-level sweep on this many cores (0 = all)")
    f.add_argument("--save", metavar="FILE", help="keep finished sweep blocks in FILE; the same command again resumes")
    f.add_argument("--receipt", metavar="FILE", help="write a receipt of this run (check it with trawl verify)")
    f.set_defaults(fn=cmd_fast)
    v = sub.add_parser("verify", help="re-run a receipt and check the output matches")
    v.add_argument("receipt")
    v.add_argument("--engine", choices=["run", "fast"], help="engine to re-run on (default: the other one)")
    v.add_argument("--save", metavar="FILE", help="the run's save file: replay it, re-running --sample blocks")
    v.add_argument("--sample", type=int, default=0, metavar="N", help="with --save: blocks of each sweep to re-run")
    v.add_argument("--seed", type=int, help="with --save: pick the same blocks again")
    v.add_argument("--max-steps", type=int, default=50_000_000)
    v.set_defaults(fn=cmd_verify)
    b = sub.add_parser("build", help="build a native program")
    b.add_argument("file"); b.add_argument("-o", "--output"); b.add_argument("--keep-c")
    b.set_defaults(fn=cmd_build)
    c = sub.add_parser("c", help="print the generated C")
    c.add_argument("file"); c.set_defaults(fn=cmd_c)
    q = sub.add_parser("repl", help="interactive sea")
    q.set_defaults(fn=cmd_repl)
    args = p.parse_args(argv)
    if not args.cmd:
        if not sys.stdin.isatty():
            p.print_help()
            return
        args.fn = cmd_repl
    from .interp import _deep
    try:
        try:
            if args.fn is cmd_repl:
                cmd_repl(args)  # each line gets its own big stack, so Ctrl+C can stop just that line
            else:
                _deep(lambda: args.fn(args))  # a big stack: deep words and deeply nested brackets both fit
        except RecursionError:
            raise TrawlError("the program nests too deep: brackets or words inside each other "
                             "thousands of levels down") from None
    except TrawlError as e:
        sys.stdout.flush()
        sys.exit(f"~ {e}")
    except BrokenPipeError:  # `trawl run x | head` stopped reading: that's fine, stop quietly
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        sys.exit(0)
    except OSError as e:
        sys.exit(f"~ {e}")
    except KeyboardInterrupt:
        sys.stdout.flush()
        print("\n~ stopped (Ctrl+C)", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
