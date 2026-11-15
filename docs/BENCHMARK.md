# Benchmark: a real research search

The job: [OEIS A343507](https://oeis.org/A343507), the least n with ((n+m)!)^2 dividing
(2n)! (Erdős problem 727). The search sieves windows of a million numbers for runs of m
numbers whose prime factors are all small, then checks each candidate exactly with
Kummer's carry count. The Trawl version is [examples/a343507.trawl](../examples/a343507.trawl).

## The new terms

The Trawl search, compiled with `trawl build`, found both terms beyond the published table:

| m | search range | answer | time |
|---|---|---|---|
| 10 | (2935782889, 19295451536] | **19295451536** | 495 s |
| 11 | (19295451536, 91328826321] | **91328826321** | 1980 s |

These two runs shared a 4-core machine with other jobs, so treat the times as rough.
The hand-written C search, run on the same machine at the same time over the same m = 11
range, took 659 s.

## Same slice, four ways

m = 10, every n in (3000000000, 3300000000], which holds no answer, so every program
scans the whole slice. One core each, Linux, gcc 13 at `-O2`, Python 3.13, Intel Xeon at 2.3 GHz.

| Program | Time | vs Trawl |
|---|---|---|
| Hand-written C (the research tool, `e727.c`) | 2.6 s | 3x faster |
| **Trawl, native** (`trawl build`) | **7.7 s** | |
| The same algorithm in plain Python ([a343507.py](../benchmarks/a343507.py)) | 60 s | 8x slower |
| The same Trawl program on the original Tide engine | 1321 s (22 min) | 170x slower |

## Honest reading

- Trawl is about **3 times slower than hand-written C** on this search. The gap is mostly
  the tag check on every number (each one might have overflowed into a big integer) and
  the stack traffic around calls. That check is what lets the same program print exact
  answers past 2^62 with no changes.
- Trawl is about **8 times faster than plain Python**, and about **170 times faster than
  the engine Tide started on**, on the identical program.
- The old engine needed 22 minutes for this slice; at that rate the m = 11 search would
  have taken about 4 days. Natively it took about half an hour.

## Run it yourself

```
cd examples
printf 'use a343507\n10 3000000000 3300000000 search .\n' > slice.trawl
trawl build slice.trawl
time ./slice                                              # prints 0: no answer in the slice
time python ../benchmarks/a343507.py 10 3000000000 3300000000
```
