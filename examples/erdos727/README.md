# Erdős problem 727 in Trawl

[Erdős problem 727](https://www.erdosproblems.com/727) asks: for a fixed k >= 2, does
((n+k)!)^2 divide (2n)! for infinitely many n? It is open, even for k = 2.

One way in is to find a formula for n, a *family*, where n+1, ..., n+k all split into
smallish factors, and then show the divisibility holds for infinitely many members.
These programs test families quickly: `judge` checks one n exactly, using Kummer's theorem
(the power of a prime p in (2n)!/(n!)^2 is the number of carries when adding n + n in base p).

| File | What it does |
|---|---|
| `judge.trawl` | the exact test: does ((n+k)!)^2 divide (2n)!, given the factors of n+1..n+k |
| `land_k3.trawl` | Johan Land's k = 3 family, n = 210t^2 + 391t + 179 |
| `quartic_k4.trawl` | a k = 4 family, n = 8t^4 - 18t^2 + 6 |
| `run_k3.trawl`, `run_k4.trawl` | ready to run |

```
trawl fast run_k4.trawl    # the t below 2000 that give k = 4: 53 of them, about a second
trawl fast run_k3.trawl    # the t below 20000 that give k = 3: 3492 of them
```

Both counts match a separate Python check using Legendre's formula.

## What is known and what is not

- **k = 3 (and so k = 2).** Johan Land posted a proof, with a Lean formalisation, that his
  family gives infinitely many n ([github.com/beetree/math_erdos_727](https://github.com/beetree/math_erdos_727)).
  It has not been reviewed yet, and the problem is still listed as open.
- **k = 4.** Nothing is known. The family n = 8t^4 - 18t^2 + 6, found by a research Claude
  in November 2026, makes n+1, ..., n+4 all split:

  ```
  n+1 = (2t^2-1)(4t^2-7)       n+2 = 2(2t^2-t-2)(2t^2+t-2)
  n+3 = (2t^2-3)(4t^2-3)       n+4 = 2(t-1)(t+1)(4t^2-5)
  ```

  and k = 4 holds for about 4% of t (707 values below 20000). **This is a lead, not a proof.**
  A steady 4% suggests infinitely many solutions, but nobody has shown it.
  Two more families behave the same way: 216t^4 + 66t^2 + 2 and 1000t^4 - 210t^2 + 7.
- **Why the quadratic families stop at k = 3.** If n = at^2 + bt + c and n+1, ..., n+4 all
  split into linear factors, four squares would have to be in arithmetic progression, which
  Fermat and Euler showed is impossible. So k = 4 needs a different shape, such as these quartics.
