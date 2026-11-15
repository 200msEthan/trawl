/* Collatz records below 10^7 (OEIS A006877) */
#include <stdio.h>
#include <stdint.h>
int main(void) {
  long best = -1;
  for (uint64_t n = 1; n < 10000000; n++) {
    uint64_t x = n; long s = 0;
    while (x > 1) { x = (x & 1) ? 3 * x + 1 : x >> 1; s++; }
    if (s > best) { best = s; printf("%llu ", (unsigned long long)n); }
  }
  printf("\n");
  return 0;
}
