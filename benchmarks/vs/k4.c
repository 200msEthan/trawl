/* t < 5000 where n = 8t^4 - 18t^2 + 6 gives ((n+4)!)^2 | (2n)!  (Erdős 727, k = 4).
   The factors of n+1..n+4 come from the family's formula, as in the Trawl version. */
#include <stdio.h>
#include <stdint.h>
typedef int64_t i64;
static i64 carries(i64 n, i64 p) {  /* carries when adding n + n in base p (Kummer) */
  i64 c = 0, cr = 0;
  while (n > 0 || cr) { cr = (2 * (n % p) + cr >= p); c += cr; n /= p; }
  return c;
}
static i64 f[11]; static int F = 11; static i64 N;
static int test(i64 p) {  /* strip p from every factor; 0 if p has too few carries */
  i64 v = 0;
  for (int i = 0; i < F; i++) while (f[i] % p == 0) { f[i] /= p; v++; }
  return 2 * v <= carries(N, p);
}
static int judge(i64 t) {
  i64 u = t * t;
  i64 g[11] = {2*u-1, 4*u-7, 2, 2*u-t-2, 2*u+t-2, 2*u-3, 4*u-3, 2, t-1, t+1, 4*u-5};
  for (int i = 0; i < F; i++) f[i] = g[i];
  N = 8*u*u - 18*u + 6;
  i64 bound = 2 * t + 2;
  for (i64 p = 2; p <= bound; p++) if (!test(p)) return 0;
  for (int i = 0; i < F; i++) if (f[i] > 1 && !test(f[i])) return 0;
  return 1;
}
int main(void) {
  int c = 0;
  for (i64 t = 2; t < 5000; t++) if (judge(t)) { printf("%lld ", (long long)t); c++; }
  printf("count %d\n", c);
  return 0;
}
