# Trawl vs PARI/GP vs C

Four "find the terms" searches, each written in all three languages. They ran on the same
machine, one core each: Linux, Intel Xeon at 2.3 GHz, gcc 13 at `-O2`, PARI/GP 2.15.4, Trawl 0.1
(`trawl build`). All three print the same answers in every search.

| Search | C | Trawl | PARI/GP |
|---|---|---|---|
| **A343507**, m = 10, n in (3·10^9, 3.3·10^9]: log sieve + exact check | 2.6 s | 7.7 s | 216 s * |
| **Collatz records** below 10^7 (A006877) | 3.0 s | 7.0 s | 386 s |
| **Powers of 2 with no digit 0**, n ≤ 30000 (A007377) | 0.05 s | 0.45 s † | 7.2 s |
| **Erdős 727, k = 4 family**, t < 5000 | 1.1 s | 2.4 s | 0.7 s ‡ |

\* PARI took 21.6 s for a tenth of the slice. Its sieve is linear, so 216 s is that time × 10.
† The same Trawl program checking one digit at a time instead of nine takes 1.4 s.
‡ PARI factors with `factor()`, which is fast and clever. The C and Trawl versions use trial
division, following the original research program.

Program size, counting code characters without spaces or comments:

| Search | C | Trawl | PARI/GP |
|---|---|---|---|
| A343507 | 2225 | 2109 | 699 |
| Collatz records | 190 | 163 | 112 |
| Powers of 2 with no 0 | 372 | 338 | 60 |
| Erdős 727, k = 4 | 788 | 1035 | 280 |

## What this says

- **On loop-heavy searches, Trawl is 28 to 55 times faster than PARI/GP and 2.2 to 3 times
  slower than hand-written C.** The gap to C comes from the tag check on every number (each one
  might have grown into a big integer) and the stack traffic around calls.
- **On big-number digit work, Trawl is 10 times slower than C.** The C program keeps 2^n in
  base 10^9 and looks at the low digits directly. Trawl divides the whole big number each time.
- **PARI wins when it has the right built-in.** Its `factor()` beats both trial-division
  versions. Trawl has no fast factoring word yet.
- **PARI programs are 1.5 to 6 times shorter.** Trawl programs are about as long as C, mostly
  because variables live in numbered seabed cells.

## Run them

```
gcc -O2 -o collatz_c collatz.c && time ./collatz_c
trawl build collatz.trawl && time ./collatz
time gp -q collatz.gp < /dev/null
```

The A343507 programs are `a343507.c` (`./a343507 10 10 3000000000 3300000000`),
`../../examples/a343507.trawl` and `a343507.gp` (`search(10, 3000000000, 3300000000)`).
The Erdős 727 Trawl program is `../../examples/erdos727/run_k4.trawl`, with `5000` in place of `2000`.
