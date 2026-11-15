/* n <= 30000 where 2^n has no digit 0 (OEIS A007377). 2^n is kept in base 10^9, lowest chunk first. */
#include <stdio.h>
#include <stdint.h>
#define BASE 1000000000u
static uint32_t d[4000];
int main(void) {
  int len = 1; d[0] = 1;
  for (int n = 1; n <= 30000; n++) {
    uint32_t carry = 0;
    for (int i = 0; i < len; i++) { uint64_t v = (uint64_t)d[i] * 2 + carry; d[i] = v % BASE; carry = v / BASE; }
    if (carry) d[len++] = carry;
    int ok = 1;
    for (int i = 0; i < len && ok; i++) {
      uint32_t c = d[i]; int digits = (i == len - 1) ? 0 : 9;
      for (int k = 0; (k < digits || c) && ok; k++, c /= 10) if (c % 10 == 0) ok = 0;
    }
    if (ok) printf("%d ", n);
  }
  printf("\n");
  return 0;
}
