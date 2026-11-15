/* trawl_big.h: whole numbers of any size for Trawl's native runtime.
 *
 * Self-contained (no GMP needed), so a Trawl program builds with any C99 compiler.
 * Magnitudes are little-endian arrays of 32-bit limbs. Values that fit in 62 bits
 * never reach this file: the runtime keeps them as tagged machine words.
 */
#ifndef TRAWL_BIG_H
#define TRAWL_BIG_H
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t limb;
typedef uint64_t dlimb;

/* A number in working form: sign (-1, 0, +1), length, limbs. */
typedef struct { int sign; int n; limb *d; } Num;

static int mag_cmp(const limb *a, int an, const limb *b, int bn) {
  if (an != bn) return an < bn ? -1 : 1;
  for (int i = an - 1; i >= 0; i--)
    if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
  return 0;
}

static int mag_norm(const limb *a, int n) { while (n > 0 && a[n - 1] == 0) n--; return n; }

/* r = a + b, r has room for max(an,bn)+1 limbs; returns length */
static int mag_add(limb *r, const limb *a, int an, const limb *b, int bn) {
  if (an < bn) { const limb *t = a; a = b; b = t; int tn = an; an = bn; bn = tn; }
  dlimb c = 0; int i;
  for (i = 0; i < bn; i++) { c += (dlimb)a[i] + b[i]; r[i] = (limb)c; c >>= 32; }
  for (; i < an; i++) { c += a[i]; r[i] = (limb)c; c >>= 32; }
  r[i] = (limb)c;
  return mag_norm(r, an + 1);
}

/* r = a - b where |a| >= |b|; returns length */
static int mag_sub(limb *r, const limb *a, int an, const limb *b, int bn) {
  int64_t br = 0; int i;
  for (i = 0; i < bn; i++) { int64_t t = (int64_t)a[i] - b[i] + br; r[i] = (limb)t; br = t < 0 ? -1 : 0; }
  for (; i < an; i++) { int64_t t = (int64_t)a[i] + br; r[i] = (limb)t; br = t < 0 ? -1 : 0; }
  return mag_norm(r, an);
}

/* r = a * b, r has room for an+bn limbs and must not alias */
static int mag_mul(limb *r, const limb *a, int an, const limb *b, int bn) {
  memset(r, 0, sizeof(limb) * (size_t)(an + bn));
  for (int i = 0; i < an; i++) {
    dlimb c = 0, ai = a[i];
    if (!ai) continue;
    for (int j = 0; j < bn; j++) { c += ai * b[j] + r[i + j]; r[i + j] = (limb)c; c >>= 32; }
    r[i + bn] = (limb)c;
  }
  return mag_norm(r, an + bn);
}

/* q = a / d (single limb), returns remainder; q may alias a */
static limb mag_divsmall(limb *q, const limb *a, int an, limb d) {
  dlimb r = 0;
  for (int i = an - 1; i >= 0; i--) { r = (r << 32) | a[i]; q[i] = (limb)(r / d); r %= d; }
  return (limb)r;
}

static int clz32(limb x) { int n = 0; if (!x) return 32; while (!(x & 0x80000000u)) { x <<= 1; n++; } return n; }

/* Knuth algorithm D. q gets an-bn+1 limbs, r gets bn limbs. Requires bn >= 2, an >= bn. */
static void mag_divmod_knuth(limb *q, limb *r, const limb *a, int an, const limb *b, int bn) {
  int s = clz32(b[bn - 1]);
  limb *u = (limb *)calloc((size_t)an + 1, sizeof(limb));
  limb *v = (limb *)calloc((size_t)bn, sizeof(limb));
  for (int i = bn - 1; i > 0; i--) v[i] = s ? (b[i] << s) | (b[i - 1] >> (32 - s)) : b[i];
  v[0] = b[0] << s;
  u[an] = s ? a[an - 1] >> (32 - s) : 0;
  for (int i = an - 1; i > 0; i--) u[i] = s ? (a[i] << s) | (a[i - 1] >> (32 - s)) : a[i];
  u[0] = a[0] << s;
  for (int j = an - bn; j >= 0; j--) {
    dlimb num = ((dlimb)u[j + bn] << 32) | u[j + bn - 1];
    dlimb qh = num / v[bn - 1], rh = num % v[bn - 1];
    while (qh >= ((dlimb)1 << 32) || qh * v[bn - 2] > ((rh << 32) | u[j + bn - 2])) {
      qh--; rh += v[bn - 1];
      if (rh >= ((dlimb)1 << 32)) break;
    }
    int64_t br = 0; dlimb c = 0;
    for (int i = 0; i < bn; i++) {
      c += qh * v[i];
      int64_t t = (int64_t)u[i + j] - (int64_t)(limb)c + br;
      u[i + j] = (limb)t; br = t >> 32; c >>= 32;
    }
    int64_t t = (int64_t)u[j + bn] - (int64_t)c + br;
    u[j + bn] = (limb)t;
    if (t < 0) { /* add back */
      qh--; dlimb c2 = 0;
      for (int i = 0; i < bn; i++) { c2 += (dlimb)u[i + j] + v[i]; u[i + j] = (limb)c2; c2 >>= 32; }
      u[j + bn] += (limb)c2;
    }
    q[j] = (limb)qh;
  }
  for (int i = 0; i < bn; i++) r[i] = s ? (u[i] >> s) | ((dlimb)u[i + 1] << (32 - s)) : u[i];
  free(u); free(v);
}
#endif
