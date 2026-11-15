/* trawl_rt.h: the native runtime for Trawl programs.
 *
 * The Trawl compiler (trawl/cgen.py) turns a .trawl program into C that includes this
 * file, then hands it to gcc or clang. Everything a program needs lives here:
 *   - numbers: 62-bit machine words that quietly turn into big numbers on overflow,
 *     so results are always exact (same answers as the reference interpreter)
 *   - the stack, the seabed (numbered memory cells), echo (state-repeat detection),
 *     worlds (wave / want / more / aim backtracking) and pearls (remembered answers)
 *   - the sea's error messages, word for word the same as the interpreter's
 */
#ifndef TRAWL_RT_H
#define TRAWL_RT_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdarg.h>
#include <setjmp.h>
#include "trawl_big.h"
#ifndef _WIN32
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <signal.h>
#include <errno.h>
#define rt_truncate(f, n) ((void)!ftruncate(fileno(f), (n)))
#else
#include <io.h>
#define rt_truncate(f, n) ((void)_chsize(_fileno(f), (long)(n)))
#endif
#ifndef TRAWL_PROG_HASH
#define TRAWL_PROG_HASH "0"
#endif

typedef int64_t V; /* a Trawl number: (n << 1) for small n, or (Big* | 1) */

#define ISSMALL(v) ((((uint64_t)(v)) & 1u) == 0)
#define BOTHSMALL(a, b) (((((uint64_t)(a)) | ((uint64_t)(b))) & 1u) == 0)
#define SMALLN(v) ((int64_t)(v) >> 1)
#define MKV(n) ((V)((uint64_t)(int64_t)(n) << 1))
#define VTRUE ((V)2)
#define VFALSE ((V)0)
#define SMALL_MIN (-((int64_t)1 << 62))
#define SMALL_MAX (((int64_t)1 << 62) - 1)
#define LIKELY(x) __builtin_expect(!!(x), 1)
#define UNLIKELY(x) __builtin_expect(!!(x), 0)

/* ------------------------------------------------------------------ errors */

static int rt_world_mode = 0; /* 1 when the program uses wave/want/more/aim */
static void rt_flush(void);
static int rt_capture = 0;    /* 1 while a sweep block's words go into wbuf instead of stdout */
static int rt_child_fd = -1;  /* in a sweep worker: where its block's result goes */
static void rt_child_died(const char *msg);
static void rt_capture_spill(void);
static void rt_stop_workers(void);

static void rt_die(const char *fmt, ...) {
  va_list ap;
  if (rt_child_fd >= 0) {
    char msg[2048];
    va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap);
    rt_child_died(msg);
  }
  rt_stop_workers();
  if (rt_capture) rt_capture_spill();
  rt_flush();
  fputs("~ ", stderr);
  va_start(ap, fmt); vfprintf(stderr, fmt, ap); va_end(ap);
  fputc('\n', stderr);
  exit(1);
}

/* ------------------------------------------------------------------ big numbers */

typedef struct Big { struct Big *next; uint32_t mark; int sign; int n; limb d[]; } Big;
#define BIGP(v) ((Big *)(uintptr_t)((uint64_t)(v) & ~(uint64_t)1))

static Big *big_list = NULL;
static size_t big_bytes = 0, big_next_gc = (size_t)64 << 20;
static int gc_due = 0; /* an int, not a V, so number stores can't alias it */
/* `x d %` then `x d /` is common (taking digits off): the remainder pass also keeps the quotient */
static Big *divc_a = NULL, *divc_q = NULL; static int64_t divc_d = 0;

static V big_alloc_from(int sign, const limb *d, int n, int permanent) {
  n = mag_norm(d, n);
  if (n == 0) return 0;
  if (n <= 2) { /* does it fit in a small? */
    uint64_t m = d[0] | (n == 2 ? (uint64_t)d[1] << 32 : 0);
    if (sign > 0 && m <= (uint64_t)SMALL_MAX) return MKV((int64_t)m);
    if (sign < 0 && m <= ((uint64_t)1 << 62)) return MKV(-(int64_t)(m - 1) - 1);
  }
  size_t bytes = sizeof(Big) + sizeof(limb) * (size_t)n;
  Big *b = (Big *)malloc(bytes);
  if (!b) rt_die("the sea ran out of room for a %d-digit number", n * 10);
  b->sign = sign; b->n = n; memcpy(b->d, d, sizeof(limb) * (size_t)n);
  b->mark = permanent ? 2 : 0;
  b->next = big_list; big_list = b; big_bytes += bytes;
  if (big_bytes > big_next_gc) gc_due = 1;
  return (V)((uint64_t)(uintptr_t)b | 1u);
}

/* Long loops inside one built-in (prime, spf) make many short-lived big numbers that the
 * garbage collector can't see. A scope frees the ones made since it began, except the live ones. */
static Big *big_scope(void) { return big_list; }
static void big_trim(Big *since, const V *live, int k) {
  divc_a = divc_q = NULL;
  Big **p = &big_list;
  while (*p && *p != since) {
    Big *b = *p; int keep = b->mark != 0;
    for (int i = 0; i < k && !keep; i++) if (!ISSMALL(live[i]) && BIGP(live[i]) == b) keep = 1;
    if (keep) p = &b->next;
    else { *p = b->next; big_bytes -= sizeof(Big) + sizeof(limb) * (size_t)b->n; free(b); }
  }
}

/* view any value as sign + limbs; buf must hold 2 limbs */
static Num num_view(V v, limb *buf) {
  Num r;
  if (ISSMALL(v)) {
    int64_t x = SMALLN(v);
    uint64_t m = x < 0 ? (uint64_t)(-(x + 1)) + 1 : (uint64_t)x;
    buf[0] = (limb)m; buf[1] = (limb)(m >> 32);
    r.sign = x < 0 ? -1 : (x > 0); r.d = buf; r.n = mag_norm(buf, 2);
  } else {
    Big *b = BIGP(v); r.sign = b->sign; r.n = b->n; r.d = b->d;
  }
  return r;
}

static int v_cmp(V a, V b) {
  if (BOTHSMALL(a, b)) return a < b ? -1 : a > b;
  limb ba[2], bb[2]; Num x = num_view(a, ba), y = num_view(b, bb);
  if (x.sign != y.sign) return x.sign < y.sign ? -1 : 1;
  int c = mag_cmp(x.d, x.n, y.d, y.n);
  return x.sign < 0 ? -c : c;
}

static V big_addsub(V a, V b, int negate_b) {
  limb ba[2], bb[2]; Num x = num_view(a, ba), y = num_view(b, bb);
  if (negate_b) y.sign = -y.sign;
  if (y.sign == 0) return a;
  if (x.sign == 0) return negate_b ? big_alloc_from(y.sign, y.d, y.n, 0) : b;
  int n = (x.n > y.n ? x.n : y.n) + 1;
  limb *r = (limb *)malloc(sizeof(limb) * (size_t)n);
  V out;
  if (x.sign == y.sign) {
    int rn = mag_add(r, x.d, x.n, y.d, y.n); out = big_alloc_from(x.sign, r, rn, 0);
  } else {
    int c = mag_cmp(x.d, x.n, y.d, y.n);
    if (c == 0) out = 0;
    else if (c > 0) { int rn = mag_sub(r, x.d, x.n, y.d, y.n); out = big_alloc_from(x.sign, r, rn, 0); }
    else { int rn = mag_sub(r, y.d, y.n, x.d, x.n); out = big_alloc_from(y.sign, r, rn, 0); }
  }
  free(r);
  return out;
}

static V big_mul(V a, V b) {
  limb ba[2], bb[2]; Num x = num_view(a, ba), y = num_view(b, bb);
  if (!x.sign || !y.sign) return 0;
  limb *r = (limb *)malloc(sizeof(limb) * (size_t)(x.n + y.n));
  int rn = mag_mul(r, x.d, x.n, y.d, y.n);
  V out = big_alloc_from(x.sign * y.sign, r, rn, 0);
  free(r);
  return out;
}

static void rt_zero_split(V a);

/* floor division and floor remainder (Python's // and %) */
static void big_divmod(V a, V b, V *qo, V *ro) {
  limb ba[2], bb[2]; Num x = num_view(a, ba), y = num_view(b, bb);
  if (!y.sign) rt_zero_split(a);
  if (!x.sign) { if (qo) *qo = 0; if (ro) *ro = 0; return; }
  limb *q = (limb *)calloc((size_t)x.n + 1, sizeof(limb));
  limb *r = (limb *)calloc((size_t)y.n + 1, sizeof(limb));
  int qn, rn;
  if (mag_cmp(x.d, x.n, y.d, y.n) < 0) { qn = 0; memcpy(r, x.d, sizeof(limb) * (size_t)x.n); rn = x.n; }
  else if (y.n == 1) { r[0] = mag_divsmall(q, x.d, x.n, y.d[0]); qn = mag_norm(q, x.n); rn = mag_norm(r, 1); }
  else { mag_divmod_knuth(q, r, x.d, x.n, y.d, y.n); qn = mag_norm(q, x.n - y.n + 1); rn = mag_norm(r, y.n); }
  int qs = x.sign * y.sign;
  if (qs < 0 && rn > 0) { /* round toward minus infinity */
    limb one = 1;
    limb *q2 = (limb *)calloc((size_t)qn + 2, sizeof(limb));
    qn = mag_add(q2, q, qn, &one, 1); free(q); q = q2;
    limb *r2 = (limb *)calloc((size_t)y.n + 1, sizeof(limb));
    rn = mag_sub(r2, y.d, y.n, r, rn); free(r); r = r2;
    if (qo) *qo = big_alloc_from(-1, q, qn, 0);
    if (ro) *ro = big_alloc_from(y.sign, r, rn, 0);
  } else {
    if (qo) *qo = big_alloc_from(qs, q, qn, 0);
    if (ro) *ro = big_alloc_from(y.sign, r, rn, 0);
  }
  free(q); free(r);
}

static char *v_to_str(V v) { /* caller frees */
  if (ISSMALL(v)) { char *s = (char *)malloc(24); snprintf(s, 24, "%lld", (long long)SMALLN(v)); return s; }
  Big *b = BIGP(v);
  limb *t = (limb *)malloc(sizeof(limb) * (size_t)b->n); memcpy(t, b->d, sizeof(limb) * (size_t)b->n);
  int n = b->n;
  size_t cap = (size_t)n * 10 + 4; char *buf = (char *)malloc(cap); size_t len = 0;
  while (n > 0) {
    limb rem = mag_divsmall(t, t, n, 1000000000u); n = mag_norm(t, n);
    for (int i = 0; i < 9; i++) { buf[len++] = (char)('0' + rem % 10); rem /= 10; if (n == 0 && rem == 0) break; }
  }
  if (b->sign < 0) buf[len++] = '-';
  for (size_t i = 0; i < len / 2; i++) { char c = buf[i]; buf[i] = buf[len - 1 - i]; buf[len - 1 - i] = c; }
  buf[len] = 0; free(t);
  return buf;
}

static V v_from_str(const char *s) { /* decimal literal, optional sign; permanent */
  int sign = 1;
  if (*s == '-') { sign = -1; s++; } else if (*s == '+') s++;
  size_t len = strlen(s); int cap = (int)(len / 9) + 3; int n = 0;
  limb *d = (limb *)calloc((size_t)cap, sizeof(limb));
  for (size_t i = 0; i < len; i++) {
    dlimb c = (dlimb)(s[i] - '0');
    for (int j = 0; j < n; j++) { c += (dlimb)d[j] * 10; d[j] = (limb)c; c >>= 32; }
    if (c) d[n++] = (limb)c;
  }
  V v = big_alloc_from(n ? sign : 0, d, n, 1);
  free(d);
  return v;
}

/* ------------------------------------------------------------------ arithmetic */

static inline V v_add(V a, V b) {
  V r;
  if (LIKELY(BOTHSMALL(a, b)) && LIKELY(!__builtin_add_overflow(a, b, &r))) return r;
  return big_addsub(a, b, 0);
}
static inline V v_sub(V a, V b) {
  V r;
  if (LIKELY(BOTHSMALL(a, b)) && LIKELY(!__builtin_sub_overflow(a, b, &r))) return r;
  return big_addsub(a, b, 1);
}
static inline V v_mul(V a, V b) {
  V r;
  if (LIKELY(BOTHSMALL(a, b)) && LIKELY(!__builtin_mul_overflow(SMALLN(a), b, &r))) return r;
  return big_mul(a, b);
}
static void rt_zero_split(V a) {
  char *s = v_to_str(a);
  rt_die("you tried to split %s into zero pieces. the ocean refuses.", s);
}
static V big_div_small(V a, int64_t y);
static V big_div_small_r(V a, int64_t y, int64_t *rem);
static inline V v_div(V a, V b) {
  if (LIKELY(BOTHSMALL(a, b))) {
    int64_t x = SMALLN(a), y = SMALLN(b);
    if (UNLIKELY(y == 0)) rt_zero_split(a);
    if (LIKELY(!(x == SMALL_MIN && y == -1))) {
      int64_t q = x / y;
      if ((x % y != 0) && ((x < 0) != (y < 0))) q--;
      return MKV(q);
    }
  }
  if (ISSMALL(b) && SMALLN(b) > 0 && SMALLN(b) < ((int64_t)1 << 32) && !ISSMALL(a) && BIGP(a)->sign > 0)
    return big_div_small(a, SMALLN(b));
  V q; big_divmod(a, b, &q, NULL); return q;
}
/* a big number divided by a small one that fits in a limb: one pass, no scratch space */
static inline limb mag_modsmall(const limb *a, int an, limb d) {
  if (d == 1) return 0;
  dlimb v = UINT64_MAX / d, r = 0;
  for (int i = an - 1; i >= 0; i--) {
    dlimb x = (r << 32) | a[i];
    r = x - (dlimb)(((unsigned __int128)x * v) >> 64) * d;
    while (r >= d) r -= d;
  }
  return (limb)r;
}
static V big_mod_small(V a, int64_t y) {
  Big *b = BIGP(a);
  uint64_t m = y < 0 ? (uint64_t)(-y) : (uint64_t)y;
  int64_t r;
  if (y > 0 && b->sign > 0 && b->n >= 8) { /* keep the quotient too, for a division that may follow */
    V q = big_div_small_r(a, y, &r);
    divc_a = b; divc_d = y; divc_q = ISSMALL(q) ? NULL : BIGP(q);
  } else r = (int64_t)mag_modsmall(b->d, b->n, (limb)m);
  if (r && (b->sign < 0) != (y < 0)) r = (int64_t)m - r; /* floor: the remainder takes the divisor's sign */
  return MKV(y < 0 ? -r : r);
}
static V big_div_small_r(V a, int64_t y, int64_t *rem) { /* a > 0 big, 0 < y < 2^32 */
  Big *b = BIGP(a);
  size_t bytes = sizeof(Big) + sizeof(limb) * (size_t)b->n;
  Big *q = (Big *)malloc(bytes);
  if (!q) rt_die("the sea ran out of room for a %d-digit number", b->n * 10);
  *rem = (int64_t)mag_divsmall(q->d, b->d, b->n, (limb)y);
  int n = mag_norm(q->d, b->n);
  if (n <= 2) { V v = big_alloc_from(1, q->d, n, 0); free(q); return v; }
  q->sign = 1; q->n = n; q->mark = 0;
  q->next = big_list; big_list = q; big_bytes += sizeof(Big) + sizeof(limb) * (size_t)n;
  if (big_bytes > big_next_gc) gc_due = 1;
  return (V)((uint64_t)(uintptr_t)q | 1u);
}
static V big_div_small(V a, int64_t y) {
  if (divc_q && divc_a == BIGP(a) && divc_d == y) return (V)((uint64_t)(uintptr_t)divc_q | 1u);
  int64_t r; return big_div_small_r(a, y, &r);
}
static inline V v_mod(V a, V b) {
  if (ISSMALL(b) && !ISSMALL(a) && SMALLN(b) != 0 && SMALLN(b) > -((int64_t)1 << 32) && SMALLN(b) < ((int64_t)1 << 32))
    return big_mod_small(a, SMALLN(b));
  if (LIKELY(BOTHSMALL(a, b))) {
    int64_t x = SMALLN(a), y = SMALLN(b);
    if (UNLIKELY(y == 0)) rt_zero_split(a);
    if (y == -1) return 0;
    int64_t r = x % y;
    if (r != 0 && ((r < 0) != (y < 0))) r += y;
    return MKV(r);
  }
  V r; big_divmod(a, b, NULL, &r); return r;
}
static inline V v_eq(V a, V b) {
  if (a == b) return VTRUE;
  if (BOTHSMALL(a, b)) return VFALSE;
  return v_cmp(a, b) == 0 ? VTRUE : VFALSE;
}
static inline V v_lt(V a, V b) { if (BOTHSMALL(a, b)) return a < b ? VTRUE : VFALSE; return v_cmp(a, b) < 0 ? VTRUE : VFALSE; }
static inline V v_gt(V a, V b) { if (BOTHSMALL(a, b)) return a > b ? VTRUE : VFALSE; return v_cmp(a, b) > 0 ? VTRUE : VFALSE; }
static inline V v_abs(V a) { return v_cmp(a, 0) < 0 ? v_sub(0, a) : a; }

static V v_power(V a, V k) {
  if (v_cmp(k, 0) < 0) {
    char *sa = v_to_str(a), *sk = v_to_str(k);
    rt_die("tide numbers are whole: %s to the power %s would be a fraction", sa, sk);
  }
  if (!ISSMALL(k)) {
    if (a == MKV(0) || a == MKV(1)) return a;
    if (a == MKV(-1)) return (BIGP(k)->d[0] & 1) ? a : MKV(1);
    rt_die("that power is too big for the sea to hold");
  }
  int64_t e = SMALLN(k);
  V result = MKV(1), base = a;
  while (e > 0) {
    if (e & 1) result = v_mul(result, base);
    e >>= 1;
    if (e) base = v_mul(base, base);
  }
  return result;
}

static void rt_no_digits(V base) {
  char *s = v_to_str(base);
  rt_die("base %s has no digits. try 2 or more.", s);
}

/* digit sum (sign = +1) or alternating digit sum (sign = -1) of |x| in base b */
static V v_digits(V x, V b, int alternate) {
  if (v_cmp(b, MKV(2)) < 0) rt_no_digits(b);
  x = v_abs(x);
  if (BOTHSMALL(x, b)) {
    uint64_t n = (uint64_t)SMALLN(x), base = (uint64_t)SMALLN(b);
    int64_t total = 0, g = 1;
    while (n) { total += g * (int64_t)(n % base); n /= base; if (alternate) g = -g; }
    return MKV(total);
  }
  if (ISSMALL(b) && SMALLN(b) < ((int64_t)1 << 32)) {
    /* divide by base^k (the biggest power that fits in a limb) and take k digits from each remainder */
    uint64_t base = (uint64_t)SMALLN(b), chunk = base; int k = 1;
    while (chunk * base <= 0xFFFFFFFFu) { chunk *= base; k++; }
    Big *xb = BIGP(x);
    int n = xb->n;
    limb *t = (limb *)malloc(sizeof(limb) * (size_t)n); memcpy(t, xb->d, sizeof(limb) * (size_t)n);
    int64_t sum = 0, g = 1; /* |sum| < 2^32 per limb pass: no overflow below about 10^9 limbs */
    while (n > 0) {
      uint64_t r = mag_divsmall(t, t, n, (limb)chunk); n = mag_norm(t, n);
      for (int i = 0; i < k && (n > 0 || r); i++) { sum += g * (int64_t)(r % base); r /= base; if (alternate) g = -g; }
    }
    free(t);
    return MKV(sum);
  }
  V total = 0; int g = 1;
  while (x != 0) {
    V q, r; big_divmod(x, b, &q, &r);
    total = g > 0 ? v_add(total, r) : v_sub(total, r);
    x = q; if (alternate) g = -g;
  }
  return total;
}

/* ------------------------------------------------------------------ primes
 * prime: strong probable-prime tests to the 13 prime bases 2..41 (exact below 3.3e24), the
 * same tests as the interpreter. spf: trial division, then Pollard-Brent rho. Numbers that
 * fit in 62 bits take a fast path with 128-bit products. */
static const int PRIME_BASES[13] = {2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41};

static inline uint64_t u_mulmod(uint64_t a, uint64_t b, uint64_t m) { return (uint64_t)((unsigned __int128)a * b % m); }
static uint64_t u_powmod(uint64_t a, uint64_t e, uint64_t m) {
  uint64_t r = 1 % m; a %= m;
  while (e) { if (e & 1) r = u_mulmod(r, a, m); a = u_mulmod(a, a, m); e >>= 1; }
  return r;
}
static int u_isprime(uint64_t n) {
  if (n < 2) return 0;
  for (int i = 0; i < 13; i++) if (n % (uint64_t)PRIME_BASES[i] == 0) return n == (uint64_t)PRIME_BASES[i];
  uint64_t d = n - 1; int s = 0;
  while (!(d & 1)) { d >>= 1; s++; }
  for (int i = 0; i < 13; i++) {
    uint64_t x = u_powmod((uint64_t)PRIME_BASES[i], d, n);
    if (x == 1 || x == n - 1) continue;
    int ok = 0;
    for (int j = 1; j < s; j++) { x = u_mulmod(x, x, n); if (x == n - 1) { ok = 1; break; } }
    if (!ok) return 0;
  }
  return 1;
}
static uint64_t u_gcd(uint64_t a, uint64_t b) { while (b) { uint64_t t = a % b; a = b; b = t; } return a; }
static uint64_t u_rho(uint64_t n) {
  for (uint64_t c = 1;; c++) {
    uint64_t y = 2, r = 1, q = 1, g = 1, x = 0, ys = 0;
    while (g == 1) {
      x = y;
      for (uint64_t i = 0; i < r; i++) y = (u_mulmod(y, y, n) + c) % n;
      for (uint64_t k = 0; k < r && g == 1; k += 128) {
        ys = y;
        for (uint64_t i = 0; i < 128 && i < r - k; i++) {
          y = (u_mulmod(y, y, n) + c) % n;
          q = u_mulmod(q, x > y ? x - y : y - x, n);
        }
        g = u_gcd(q, n);
      }
      r *= 2;
    }
    if (g == n) {
      g = 1;
      while (g == 1) { ys = (u_mulmod(ys, ys, n) + c) % n; g = u_gcd(x > ys ? x - ys : ys - x, n); }
    }
    if (g != n) return g;
  }
}
static uint64_t u_spf(uint64_t n) {
  if (!(n & 1)) return 2;
  for (uint64_t i = 3; i < 1000; i += 2) {
    if (i * i > n) return n;
    if (n % i == 0) return i;
  }
  if (u_isprime(n)) return n;
  uint64_t f = u_rho(n), a = u_spf(f), b = u_spf(n / f);
  return a < b ? a : b;
}

/* the same for numbers below 2^127, in Montgomery form with 128-bit words: much faster than
 * the general big-number path, which allocates a new number for every step */
typedef unsigned __int128 u128;
typedef struct { u128 n, ni, one, r2; } Mont;
static inline void w_mul(u128 a, u128 b, u128 *hi, u128 *lo) {
  uint64_t a0 = (uint64_t)a, a1 = (uint64_t)(a >> 64), b0 = (uint64_t)b, b1 = (uint64_t)(b >> 64);
  u128 p00 = (u128)a0 * b0, p01 = (u128)a0 * b1, p10 = (u128)a1 * b0, p11 = (u128)a1 * b1;
  u128 mid = (p00 >> 64) + (uint64_t)p01 + (uint64_t)p10;
  *lo = (u128)(uint64_t)p00 | (mid << 64);
  *hi = p11 + (p01 >> 64) + (p10 >> 64) + (mid >> 64);
}
static inline u128 m_redc(const Mont *M, u128 hi, u128 lo) {
  u128 m = lo * M->ni, mh, ml;
  w_mul(m, M->n, &mh, &ml);
  u128 t = hi + mh + (lo != 0);
  return t >= M->n ? t - M->n : t;
}
static inline u128 m_mul(const Mont *M, u128 a, u128 b) { u128 hi, lo; w_mul(a, b, &hi, &lo); return m_redc(M, hi, lo); }
static inline u128 m_add(const Mont *M, u128 a, u128 b) { u128 t = a + b; return t >= M->n ? t - M->n : t; }
static Mont m_init(u128 n) { /* n odd, 3 <= n < 2^127 */
  Mont M; M.n = n;
  u128 inv = n; for (int i = 0; i < 7; i++) inv *= 2 - n * inv;
  M.ni = (u128)0 - inv;
  M.one = ((u128)0 - n) % n; /* 2^128 mod n */
  u128 t = M.one; for (int i = 0; i < 128; i++) { t <<= 1; if (t >= n) t -= n; }
  M.r2 = t;
  return M;
}
static inline u128 m_to(const Mont *M, u128 x) { return m_mul(M, x % M->n, M->r2); }
static u128 m_pow(const Mont *M, u128 a, u128 e) { u128 r = M->one; while (e) { if (e & 1) r = m_mul(M, r, a); a = m_mul(M, a, a); e >>= 1; } return r; }
static int w_isprime(u128 n) { /* n odd, has no prime factor below 1000, n < 2^127 */
  Mont M = m_init(n);
  u128 d = n - 1; int s = 0;
  while (!(d & 1)) { d >>= 1; s++; }
  u128 minus1 = M.n - M.one;
  for (int i = 0; i < 13; i++) {
    u128 x = m_pow(&M, m_to(&M, (u128)PRIME_BASES[i]), d);
    if (x == M.one || x == minus1) continue;
    int ok = 0;
    for (int j = 1; j < s; j++) { x = m_mul(&M, x, x); if (x == minus1) { ok = 1; break; } }
    if (!ok) return 0;
  }
  return 1;
}
static u128 w_gcd(u128 a, u128 b) { while (b) { u128 t = a % b; a = b; b = t; } return a; }
static u128 w_rho(u128 n) { /* Brent's rho on x -> x^2 + c, all in Montgomery form (gcds are unchanged by it) */
  Mont M = m_init(n);
  for (uint64_t c0 = 1;; c0++) {
    u128 c = m_to(&M, c0), y = m_to(&M, 2), q = M.one, g = 1, x = 0, ys = 0;
    uint64_t r = 1;
    while (g == 1) {
      x = y;
      for (uint64_t i = 0; i < r; i++) y = m_add(&M, m_mul(&M, y, y), c);
      for (uint64_t k = 0; k < r && g == 1; k += 128) {
        ys = y;
        for (uint64_t i = 0; i < 128 && i < r - k; i++) {
          y = m_add(&M, m_mul(&M, y, y), c);
          q = m_mul(&M, q, x > y ? x - y : y - x);
        }
        g = w_gcd(q, n);
      }
      r *= 2;
    }
    if (g == n) {
      g = 1;
      while (g == 1) { ys = m_add(&M, m_mul(&M, ys, ys), c); g = w_gcd(x > ys ? x - ys : ys - x, n); }
    }
    if (g != n) return g;
  }
}
static u128 w_spf(u128 n) { /* n has no prime factor below 1000 */
  if (n >> 62 == 0) return u_spf((uint64_t)n);
  if (w_isprime(n)) return n;
  u128 f = w_rho(n), a = w_spf(f), b = w_spf(n / f);
  return a < b ? a : b;
}
static int v_to_w(V v, u128 *out) { /* a positive number below 2^127? */
  if (ISSMALL(v)) { if (SMALLN(v) <= 0) return 0; *out = (u128)SMALLN(v); return 1; }
  Big *b = BIGP(v);
  if (b->sign <= 0 || b->n > 4 || (b->n == 4 && b->d[3] >> 31)) return 0;
  u128 x = 0; for (int i = b->n - 1; i >= 0; i--) x = (x << 32) | b->d[i];
  *out = x; return 1;
}
static V w_to_v(u128 x) { limb d[4]; for (int i = 0; i < 4; i++) { d[i] = (limb)x; x >>= 32; } return big_alloc_from(1, d, 4, 0); }

/* the same, for big numbers */
static V v_powmod(V a, V e, V m) {
  Big *scope = big_scope();
  V r = MKV(1);
  a = v_mod(a, m);
  while (v_cmp(e, 0) > 0) {
    V q, bit; big_divmod(e, MKV(2), &q, &bit);
    if (bit != 0) r = v_mod(v_mul(r, a), m);
    a = v_mod(v_mul(a, a), m);
    e = q;
    V live[3] = {r, a, e}; big_trim(scope, live, 3);
  }
  return r;
}
static int big_isprime(V n) {
  for (int i = 0; i < 13; i++) if (v_mod(n, MKV(PRIME_BASES[i])) == 0) return 0; /* n is big, so not the prime itself */
  u128 w; if (v_to_w(n, &w)) return w_isprime(w);
  V nm1 = v_sub(n, MKV(1)), d = nm1; int s = 0;
  for (;;) { V q, r; big_divmod(d, MKV(2), &q, &r); if (r != 0) break; d = q; s++; }
  for (int i = 0; i < 13; i++) {
    V x = v_powmod(MKV(PRIME_BASES[i]), d, n);
    if (x == MKV(1) || v_cmp(x, nm1) == 0) continue;
    int ok = 0;
    Big *scope = big_scope();
    for (int j = 1; j < s; j++) {
      x = v_mod(v_mul(x, x), n);
      if (v_cmp(x, nm1) == 0) { ok = 1; break; }
      big_trim(scope, &x, 1);
    }
    if (!ok) return 0;
  }
  return 1;
}
static V v_gcd(V a, V b) {
  Big *scope = big_scope();
  a = v_abs(a); b = v_abs(b);
  while (b != 0) { V t = v_mod(a, b); a = b; b = t; V live[2] = {a, b}; big_trim(scope, live, 2); }
  return a;
}
static V big_rho(V n) {
  Big *scope = big_scope();
  for (int64_t c = 1;; c++) {
    V y = MKV(2), q = MKV(1), g = MKV(1), x = 0, ys = 0, C = MKV(c);
    int64_t r = 1;
    while (g == MKV(1)) {
      x = y;
      for (int64_t i = 0; i < r; i++) { y = v_mod(v_add(v_mul(y, y), C), n); V live[3] = {x, y, q}; big_trim(scope, live, 3); }
      for (int64_t k = 0; k < r && g == MKV(1); k += 128) {
        ys = y;
        for (int64_t i = 0; i < 128 && i < r - k; i++) {
          y = v_mod(v_add(v_mul(y, y), C), n);
          q = v_mod(v_mul(q, v_abs(v_sub(x, y))), n);
          V live[4] = {x, y, ys, q}; big_trim(scope, live, 4);
        }
        g = v_gcd(q, n);
      }
      r *= 2;
    }
    if (v_cmp(g, n) == 0) {
      g = MKV(1);
      while (g == MKV(1)) { ys = v_mod(v_add(v_mul(ys, ys), C), n); g = v_gcd(v_sub(x, ys), n); V live[3] = {x, ys, g}; big_trim(scope, live, 3); }
    }
    if (v_cmp(g, n) != 0) return g;
  }
}
static V big_spf(V n) {
  if (ISSMALL(n)) return MKV(u_spf((uint64_t)SMALLN(n)));
  if (v_mod(n, MKV(2)) == 0) return MKV(2);
  for (int64_t i = 3; i < 1000; i += 2) if (v_mod(n, MKV(i)) == 0) return MKV(i);
  u128 w; if (v_to_w(n, &w)) return w_to_v(w_spf(w));
  if (big_isprime(n)) return n;
  V f = big_rho(n), a = big_spf(f), b = big_spf(v_div(n, f));
  return v_cmp(a, b) < 0 ? a : b;
}

static V v_prime(V n) {
  if (ISSMALL(n)) return SMALLN(n) >= 2 && u_isprime((uint64_t)SMALLN(n)) ? MKV(1) : MKV(0);
  return v_cmp(n, 0) > 0 && big_isprime(n) ? MKV(1) : MKV(0);
}
static V v_spf(V n) {
  if (v_cmp(n, MKV(2)) < 0) { char *s = v_to_str(n); rt_die("spf needs a whole number of 2 or more, not %s", s); }
  return big_spf(n);
}

/* ------------------------------------------------------------------ output */

static char *wbuf = NULL; static size_t wbuf_len = 0, wbuf_cap = 0; /* this world's words */

static void rt_out(const char *s, size_t n) {
  if (rt_world_mode || rt_capture) {
    if (wbuf_len + n + 1 > wbuf_cap) {
      wbuf_cap = (wbuf_len + n + 1) * 2; wbuf = (char *)realloc(wbuf, wbuf_cap);
    }
    memcpy(wbuf + wbuf_len, s, n); wbuf_len += n;
  } else {
    fwrite(s, 1, n, stdout);
    if (memchr(s, '\n', n)) fflush(stdout);
  }
}
static void rt_flush(void) { fflush(stdout); }
static void rt_capture_spill(void) { rt_capture = 0; fwrite(wbuf, 1, wbuf_len, stdout); wbuf_len = 0; }
static void rt_puts(const char *s) { rt_out(s, strlen(s)); }

static void rt_print(V v) {
  char tmp[32];
  if (ISSMALL(v)) { int n = snprintf(tmp, sizeof tmp, "%lld ", (long long)SMALLN(v)); rt_out(tmp, (size_t)n); return; }
  char *s = v_to_str(v); rt_out(s, strlen(s)); rt_out(" ", 1); free(s);
}

static void rt_emit(V v) {
  if (!ISSMALL(v) || SMALLN(v) < 0 || SMALLN(v) > 0x10FFFF) {
    char *s = v_to_str(v);
    rt_die("emit wants a character number from 0 to 1114111, not %s", s);
  }
  uint32_t c = (uint32_t)SMALLN(v); char u[4]; size_t n;
  if (c < 0x80) { u[0] = (char)c; n = 1; }
  else if (c < 0x800) { u[0] = (char)(0xC0 | (c >> 6)); u[1] = (char)(0x80 | (c & 63)); n = 2; }
  else if (c < 0x10000) { u[0] = (char)(0xE0 | (c >> 12)); u[1] = (char)(0x80 | ((c >> 6) & 63)); u[2] = (char)(0x80 | (c & 63)); n = 3; }
  else { u[0] = (char)(0xF0 | (c >> 18)); u[1] = (char)(0x80 | ((c >> 12) & 63)); u[2] = (char)(0x80 | ((c >> 6) & 63)); u[3] = (char)(0x80 | (c & 63)); n = 4; }
  rt_out(u, n);
}

/* ------------------------------------------------------------------ the stack */

static V *S = NULL; static long CAP = 0;

static void rt_grow(long need) {
  long cap = CAP ? CAP : 1024;
  while (cap < need) cap *= 2;
  if (cap > ((long)1 << 26))
    rt_die("the stack grew past 67 million numbers. is a loop pushing without popping?");
  S = (V *)realloc(S, sizeof(V) * (size_t)cap);
  if (!S) rt_die("the stack grew past what the sea can hold");
  CAP = cap;
}
#define ENSURE(n) do { if (UNLIKELY((long)(n) > CAP)) rt_grow((long)(n)); } while (0)

static void rt_underflow(const char *word, int want) {
  rt_die("the tide went out too far: `%s` wanted %d number%s, the shore was empty", word, want, want > 1 ? "s" : "");
}

static void rt_dots(long sp) {
  rt_out("<", 1);
  for (long i = 0; i < sp; i++) {
    char *s = v_to_str(S[i]); rt_out(s, strlen(s)); free(s);
    if (i + 1 < sp) rt_out(" ", 1);
  }
  rt_out("> ", 2);
}
static void rt_fold(long sp) { for (long i = 0, j = sp - 1; i < j; i++, j--) { V t = S[i]; S[i] = S[j]; S[j] = t; } }

/* ------------------------------------------------------------------ the seabed */

#ifndef BED_OFF
#define BED_OFF 4096 /* addresses from -BED_OFF up live in one flat array */
#endif
#ifndef BED_INIT
#define BED_INIT 65536
#endif
#define BED_DENSE_MAX ((int64_t)1 << 28)

/* bed_size is 32-bit on purpose: a different type from V, so stores of numbers can't alias it
   and the compiler keeps it in a register */
static V *bed = NULL; static uint32_t bed_size = 0;
typedef struct { int64_t key; V val; int full; } Cell;
static Cell *hb = NULL; static int64_t hb_cap = 0, hb_n = 0; /* the far seabed: a hash table */

static uint64_t mix64(uint64_t x) { x ^= x >> 33; x *= 0xff51afd7ed558ccdULL; x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL; x ^= x >> 33; return x; }

static void hb_put(int64_t k, V v);
static Cell *hb_find(int64_t k) {
  if (!hb_cap) return NULL;
  uint64_t i = mix64((uint64_t)k) & (uint64_t)(hb_cap - 1);
  while (hb[i].full) { if (hb[i].key == k) return &hb[i]; i = (i + 1) & (uint64_t)(hb_cap - 1); }
  return NULL;
}
static void hb_put(int64_t k, V v) {
  Cell *c = hb_find(k);
  if (c) { c->val = v; return; }
  if (v == 0) return;
  if ((hb_n + 1) * 2 > hb_cap) {
    Cell *old = hb; int64_t oc = hb_cap;
    hb_cap = hb_cap ? hb_cap * 2 : 1024; hb = (Cell *)calloc((size_t)hb_cap, sizeof(Cell)); hb_n = 0;
    for (int64_t i = 0; i < oc; i++) if (old[i].full && old[i].val) hb_put(old[i].key, old[i].val);
    free(old);
  }
  uint64_t i = mix64((uint64_t)k) & (uint64_t)(hb_cap - 1);
  while (hb[i].full) i = (i + 1) & (uint64_t)(hb_cap - 1);
  hb[i].full = 1; hb[i].key = k; hb[i].val = v; hb_n++;
}

static void bed_init(int64_t size) {
  bed_size = (uint32_t)(size < BED_INIT ? BED_INIT : size);
  bed = (V *)calloc((size_t)bed_size, sizeof(V));
  if (!bed) rt_die("the seabed could not be laid out");
}

static void bed_grow(int64_t idx) {
  int64_t ns = bed_size * 2; if (ns <= idx) ns = idx + 1;
  if (ns > BED_DENSE_MAX) ns = BED_DENSE_MAX;
  V *nb = (V *)realloc(bed, sizeof(V) * (size_t)ns);
  if (!nb) rt_die("the seabed could not grow");
  memset(nb + bed_size, 0, sizeof(V) * (size_t)(ns - bed_size));
  int64_t old = bed_size; bed = nb; bed_size = (uint32_t)ns;
  if (hb_n) { /* move far cells that are now near */
    for (int64_t i = 0; i < hb_cap; i++) {
      if (!hb[i].full) continue;
      int64_t j = hb[i].key + BED_OFF;
      if (j >= old && j < ns && hb[i].val) { bed[j] = hb[i].val; hb[i].val = 0; }
    }
  }
}

static int64_t rt_addr(V a) {
  if (!ISSMALL(a)) { char *s = v_to_str(a); rt_die("seabed addresses must be smaller than 2^62, not %s", s); }
  return SMALLN(a);
}

/* near index of a tagged address: rotating right by one turns (n<<1) into n and sends any
   big number (low bit 1) to a huge index, so one unsigned compare checks both at once */
static inline uint64_t near_index(V a) { uint64_t x = (uint64_t)a + 2 * (uint64_t)BED_OFF; return (x >> 1) | (x << 63); }
static V rt_dig_far(V a) { Cell *c = hb_find(rt_addr(a)); return c ? c->val : 0; }
static inline V rt_dig(V a) {
  uint64_t j = near_index(a);
  if (LIKELY(j < (uint64_t)bed_size)) return bed[j];
  return rt_dig_far(a);
}
static void rt_bury_far(V a, V v);
static inline void rt_bury(V a, V v) {
  uint64_t j0 = near_index(a);
  if (LIKELY(j0 < (uint64_t)bed_size)) { bed[j0] = v; return; }
  rt_bury_far(a, v);
}
static void rt_bury_far(V a, V v) {
  int64_t k = rt_addr(a), j = k + BED_OFF;
  if (j >= bed_size && j < BED_DENSE_MAX && (j < bed_size * 2 || j < ((int64_t)1 << 22))) {
    bed_grow(j); bed[j] = v; return;
  }
  hb_put(k, v);
}
static void rt_drain(void) {
  memset(bed, 0, sizeof(V) * (size_t)bed_size);
  if (hb) memset(hb, 0, sizeof(Cell) * (size_t)hb_cap);
  hb_n = 0;
}

typedef struct { int64_t k; V v; } KV;
static int kv_cmp(const void *a, const void *b) { int64_t x = ((const KV *)a)->k, y = ((const KV *)b)->k; return x < y ? -1 : x > y; }
static void rt_dotbed(void) {
  int64_t n = 0, cap = 64; KV *all = (KV *)malloc(sizeof(KV) * (size_t)cap);
  for (int64_t j = 0; j < (int64_t)bed_size; j++) if (bed[j]) { if (n == cap) { cap *= 2; all = (KV *)realloc(all, sizeof(KV) * (size_t)cap); } all[n].k = j - BED_OFF; all[n].v = bed[j]; n++; }
  for (int64_t i = 0; i < hb_cap; i++) if (hb[i].full && hb[i].val) { if (n == cap) { cap *= 2; all = (KV *)realloc(all, sizeof(KV) * (size_t)cap); } all[n].k = hb[i].key; all[n].v = hb[i].val; n++; }
  qsort(all, (size_t)n, sizeof(KV), kv_cmp);
  rt_out("{", 1);
  for (int64_t i = 0; i < n; i++) {
    char head[32]; int hl = snprintf(head, sizeof head, "%s%lld:", i ? " " : "", (long long)all[i].k);
    rt_out(head, (size_t)hl);
    char *s = v_to_str(all[i].v); rt_out(s, strlen(s)); free(s);
  }
  rt_out("} ", 2);
  free(all);
}

/* ------------------------------------------------------------------ echo */

typedef struct { uint64_t h; int64_t len; V *vals; int64_t when; } Echo;
static Echo *echoes = NULL; static int64_t echo_cap = 0, echo_used = 0, echo_n = 0;

static uint64_t v_hash(V v) {
  if (ISSMALL(v)) return mix64((uint64_t)v);
  Big *b = BIGP(v); uint64_t h = (uint64_t)b->sign * 0x9e3779b97f4a7c15ULL;
  for (int i = 0; i < b->n; i++) h = mix64(h ^ b->d[i]);
  return h;
}
static int echo_same(const Echo *e, uint64_t h, const V *vals, int64_t len) {
  if (e->h != h || e->len != len) return 0;
  for (int64_t i = 0; i < len; i++) if (v_eq(e->vals[i], vals[i]) != VTRUE) return 0;
  return 1;
}
static void echo_insert(uint64_t h, V *vals, int64_t len, int64_t when) {
  if ((echo_used + 1) * 2 > echo_cap) {
    Echo *old = echoes; int64_t oc = echo_cap;
    echo_cap = echo_cap ? echo_cap * 2 : 1024; echoes = (Echo *)calloc((size_t)echo_cap, sizeof(Echo)); echo_used = 0;
    for (int64_t i = 0; i < oc; i++) if (old[i].vals) echo_insert(old[i].h, old[i].vals, old[i].len, old[i].when);
    free(old);
  }
  uint64_t i = h & (uint64_t)(echo_cap - 1);
  while (echoes[i].vals) i = (i + 1) & (uint64_t)(echo_cap - 1);
  echoes[i].h = h; echoes[i].len = len; echoes[i].vals = vals; echoes[i].when = when; echo_used++;
}
/* lo hi echo: has the seabed from lo to hi looked exactly like this before? */
static V rt_echo(V lo, V hi) {
  int64_t a = rt_addr(lo), b = rt_addr(hi), len = b >= a ? b - a + 1 : 0;
  V *vals = (V *)malloc(sizeof(V) * (size_t)(len ? len : 1));
  uint64_t h = 0x1234567ULL + (uint64_t)len;
  for (int64_t i = 0; i < len; i++) { vals[i] = rt_dig(MKV(a + i)); h = mix64(h ^ v_hash(vals[i])); }
  V result = 0;
  if (echo_cap) {
    uint64_t i = h & (uint64_t)(echo_cap - 1);
    while (echoes[i].vals) {
      if (echo_same(&echoes[i], h, vals, len)) {
        result = MKV(echo_n - echoes[i].when); echoes[i].when = echo_n; echo_n++; free(vals);
        return result;
      }
      i = (i + 1) & (uint64_t)(echo_cap - 1);
    }
  }
  echo_insert(h, vals, len, echo_n); echo_n++;
  return 0;
}
static void rt_hush(void) {
  for (int64_t i = 0; i < echo_cap; i++) free(echoes[i].vals);
  free(echoes); echoes = NULL; echo_cap = echo_used = echo_n = 0;
}

/* ------------------------------------------------------------------ pearls */

typedef struct { V key, val; int full; } Pearl;
typedef struct { Pearl *t; int64_t cap, n; int on; } Shell;
#ifndef N_PEARL_WORDS
#define N_PEARL_WORDS 1
#endif
static Shell shells[N_PEARL_WORDS];

static Pearl *shell_find(Shell *s, V k, uint64_t h) {
  if (!s->cap) return NULL;
  uint64_t i = h & (uint64_t)(s->cap - 1);
  while (s->t[i].full) { if (v_eq(s->t[i].key, k) == VTRUE) return &s->t[i]; i = (i + 1) & (uint64_t)(s->cap - 1); }
  return NULL;
}
static void shell_put(Shell *s, V k, V v) {
  if ((s->n + 1) * 2 > s->cap) {
    Pearl *old = s->t; int64_t oc = s->cap;
    s->cap = s->cap ? s->cap * 2 : 256; s->t = (Pearl *)calloc((size_t)s->cap, sizeof(Pearl)); s->n = 0;
    for (int64_t i = 0; i < oc; i++) if (old[i].full) shell_put(s, old[i].key, old[i].val);
    free(old);
  }
  uint64_t i = v_hash(k) & (uint64_t)(s->cap - 1);
  while (s->t[i].full) i = (i + 1) & (uint64_t)(s->cap - 1);
  s->t[i].full = 1; s->t[i].key = k; s->t[i].val = v; s->n++;
}
typedef long (*WordFn)(long);
static long rt_call_pearl(int id, WordFn fn, const char *name, long sp) {
  Shell *s = &shells[id];
  if (!s->on) return fn(sp);
  if (sp < 1) rt_underflow(name, 1);
  V n = S[sp - 1];
  Pearl *p = shell_find(s, n, v_hash(n));
  if (p) { S[sp - 1] = p->val; return sp; }
  sp = fn(sp);
  if (sp < 1) rt_underflow(name, 1);
  shell_put(s, n, S[sp - 1]);
  return sp;
}
/* ------------------------------------------------------------------ sweep: many cores, and resume */
/* `lo hi sweep word` runs word on every n in [lo, hi) and counts the nonzero answers.
   A top-level sweep is cut into SWEEP_BLOCKS blocks. With --jobs N, N worker processes (fork) run
   blocks side by side; each worker keeps its block's printed words and the parent prints them in n
   order, so the output is exactly what one core prints. With --save FILE, every finished block (its
   count and its words) is written to FILE, and a rerun of the same program replays those blocks
   instead of running them again. Changes a block makes to the seabed or pearls stay in its own sea. */

#define SWEEP_BLOCKS 256
static int rt_jobs = 1;
static const char *rt_save_path = NULL;
static int rt_sweep_depth = 0, rt_in_worker = 0;
static long rt_sweep_no = 0;
typedef struct { long no; int64_t lo, hi, a, b, count; char *out; size_t len; long off; } SaveRec;
static SaveRec *saved = NULL; static long saved_n = 0, saved_pos = 0;
static FILE *save_fh = NULL;

static void rt_sweep_shape(const char *name, int64_t n, long got) {
  rt_die("`sweep %s` wants `%s` to turn each n into one answer, but at n = %lld it left %ld number%s in its place",
         name, name, (long long)n, got, got == 1 ? "" : "s");
}

static void rt_write_all(int fd, const void *p, size_t n) {
#ifndef _WIN32
  const char *c = (const char *)p;
  while (n) { ssize_t k = write(fd, c, n); if (k <= 0) _exit(3); c += k; n -= (size_t)k; }
#else
  (void)fd; (void)p; (void)n;
#endif
}
static void rt_child_died(const char *msg) { /* a worker hit an error: hand its words and the message to the parent */
#ifndef _WIN32
  int64_t c = -1; uint64_t len = wbuf_len, ml = strlen(msg);
  rt_write_all(rt_child_fd, &c, 8); rt_write_all(rt_child_fd, &len, 8); rt_write_all(rt_child_fd, wbuf, wbuf_len);
  rt_write_all(rt_child_fd, &ml, 8); rt_write_all(rt_child_fd, msg, ml);
  _exit(1);
#else
  (void)msg;
#endif
}

static void save_open(void) {
  FILE *f = fopen(rt_save_path, "rb");
  char head[64]; snprintf(head, sizeof head, "trawl-save 1 %s\n", TRAWL_PROG_HASH);
  long good = 0;
  if (f) {
    fseek(f, 0, SEEK_END); long size = ftell(f); fseek(f, 0, SEEK_SET);
    char *all = (char *)malloc((size_t)size + 1);
    size_t got = fread(all, 1, (size_t)size, f); fclose(f); all[got] = 0;
    size_t hl = strlen(head);
    if (got >= hl && !memcmp(all, head, hl)) {
      size_t pos = hl; good = (long)hl;
      for (;;) {
        long no; long long lo, hi, a, b, c; unsigned long long len; int used = 0;
        char *nl = memchr(all + pos, '\n', got - pos);
        if (!nl) break;
        *nl = 0;
        if (sscanf(all + pos, "B %ld %lld %lld %lld %lld %lld %llu%n", &no, &lo, &hi, &a, &b, &c, &len, &used) != 7) break;
        size_t body = (size_t)(nl - all) + 1;
        if (body + len + 1 > got || all[body + len] != '\n') break;
        saved = (SaveRec *)realloc(saved, sizeof(SaveRec) * (size_t)(saved_n + 1));
        SaveRec *r = &saved[saved_n++];
        r->no = no; r->lo = lo; r->hi = hi; r->a = a; r->b = b; r->count = c; r->len = (size_t)len; r->off = good;
        r->out = (char *)malloc((size_t)len + 1); memcpy(r->out, all + body, (size_t)len);
        pos = body + (size_t)len + 1; good = (long)pos;
      }
    } else if (got) {
      fprintf(stderr, "~ note: %s was saved by a different program (or an older Trawl), so this run starts over\n", rt_save_path);
    }
    free(all);
  }
  save_fh = fopen(rt_save_path, good ? "r+b" : "wb");
  if (!save_fh) rt_die("can't write the save file %s", rt_save_path);
  if (good) { fseek(save_fh, good, SEEK_SET); }
  else { fputs(head, save_fh); good = (long)strlen(head); }
  fflush(save_fh);
  rt_truncate(save_fh, good);
  if (saved_n) fprintf(stderr, "~ resuming from %s: %ld finished block%s\n", rt_save_path, saved_n, saved_n == 1 ? "" : "s");
}
static void save_forget_rest(void) { /* the run took a different path from the saved one: drop what's left */
  if (saved_pos < saved_n) {
    fflush(save_fh); fseek(save_fh, saved[saved_pos].off, SEEK_SET);
    rt_truncate(save_fh, saved[saved_pos].off);
    saved_n = saved_pos;
  }
}
static void save_block(long no, int64_t lo, int64_t hi, int64_t a, int64_t b, int64_t c, const char *out, size_t len) {
  if (!save_fh) return;
  fprintf(save_fh, "B %ld %lld %lld %lld %lld %lld %llu\n", no, (long long)lo, (long long)hi, (long long)a, (long long)b,
          (long long)c, (unsigned long long)len);
  fwrite(out, 1, len, save_fh); fputc('\n', save_fh); fflush(save_fh);
}

#ifdef _WIN32
static void rt_stop_workers(void) {}
#endif
static int64_t sweep_run(int pid, WordFn fn, const char *name, int64_t a, int64_t b, long sp) {
  int64_t c = 0;
  for (int64_t n = a; n < b; n++) {
    ENSURE(sp + 1); S[sp] = MKV(n);
    long sp2 = pid >= 0 ? rt_call_pearl(pid, fn, name, sp + 1) : fn(sp + 1);
    if (sp2 != sp + 1) rt_sweep_shape(name, n, sp2 - sp);
    if (S[sp] != VFALSE) c++;
  }
  return c;
}

#ifndef _WIN32
typedef struct { pid_t pid; FILE *f; int done, status; } Worker;
static Worker *sw_w = NULL; static int64_t sw_from = 0, sw_to = 0;
static void rt_stop_workers(void) { /* the parent is stopping: take the workers still running with it */
  if (!sw_w || rt_in_worker) return;
  Worker *w = sw_w; sw_w = NULL;
  for (int64_t k = sw_from; k < sw_to; k++) if (!w[k].done) { int st; kill(w[k].pid, SIGKILL); waitpid(w[k].pid, &st, 0); w[k].done = 1; }
}
static void sweep_take(Worker *w, int64_t *count, long no, int64_t lo, int64_t hi, int64_t a, int64_t b) {
  int64_t c = 0; uint64_t len = 0;
  rewind(w->f);
  if (fread(&c, 8, 1, w->f) != 1 || fread(&len, 8, 1, w->f) != 1)
    rt_die("a sweep worker stopped without an answer (n from %lld to %lld)", (long long)a, (long long)(b - 1));
  char *out = (char *)malloc((size_t)len + 1);
  if (len && fread(out, 1, (size_t)len, w->f) != len) rt_die("a sweep worker's answer was cut short");
  rt_out(out, (size_t)len);
  if (c < 0) {
    uint64_t ml = 0; char msg[2049] = {0};
    if (fread(&ml, 8, 1, w->f) == 1 && ml < sizeof msg && fread(msg, 1, (size_t)ml, w->f) == ml) msg[ml] = 0;
    fclose(w->f);
    rt_die("%s", msg);
  }
  fclose(w->f);
  save_block(no, lo, hi, a, b, c, out, (size_t)len);
  free(out);
  *count += c;
}
#endif

static long rt_sweep(int pid, WordFn fn, const char *name, long sp) {
  if (sp < 2) rt_underflow("sweep", 2);
  V vlo = S[sp - 2], vhi = S[sp - 1];
  sp -= 2;
  if (!BOTHSMALL(vlo, vhi)) {
    char *x = v_to_str(vlo), *y = v_to_str(vhi);
    rt_die("`sweep` runs from %s up to %s, but both ends must lie between -2^62 and 2^62 - 1", x, y);
  }
  int64_t lo = SMALLN(vlo), hi = SMALLN(vhi), count = 0;
  int blocked = (rt_jobs > 1 || rt_save_path) && !rt_sweep_depth && !rt_in_worker && !rt_world_mode && hi > lo;
  rt_sweep_depth++;
  if (!blocked) {
    if (hi > lo) count = sweep_run(pid, fn, name, lo, hi, sp);
    rt_sweep_depth--;
    ENSURE(sp + 1); S[sp] = MKV(count);
    return sp + 1;
  }
  long no = rt_sweep_no++;
  int64_t span = hi - lo, nb = span < SWEEP_BLOCKS ? span : SWEEP_BLOCKS, i = 0;
#define BLOCK_A(k) (lo + (int64_t)((__int128)span * (k) / nb))
  /* blocks finished in a saved run come back first, words and all */
  while (i < nb && saved_pos < saved_n) {
    SaveRec *r = &saved[saved_pos];
    if (r->no != no || r->lo != lo || r->hi != hi || r->a != BLOCK_A(i) || r->b != BLOCK_A(i + 1)) { save_forget_rest(); break; }
    rt_out(r->out, r->len); count += r->count; saved_pos++; i++;
  }
  if (i < nb) save_forget_rest();
#ifndef _WIN32
  if (rt_jobs > 1) {
    int64_t first = i, next = i, emit = i, window = (int64_t)rt_jobs * 4;
    Worker *w = (Worker *)calloc((size_t)nb, sizeof(Worker));
    int running = 0;
    sw_w = w;
    signal(SIGCHLD, SIG_DFL); /* if SIGCHLD came in ignored, finished workers would vanish before wait() saw them */
    while (emit < nb) {
      sw_from = emit; sw_to = next;
      while (running < rt_jobs && next < nb && next - emit < window) {
        fflush(stdout);
        if (save_fh) fflush(save_fh);
        FILE *f = tmpfile();
        if (!f) rt_die("couldn't make a scratch file for a sweep worker");
        pid_t p = fork();
        if (p < 0) rt_die("couldn't start a sweep worker (fork failed)");
        if (p == 0) {
          rt_in_worker = 1; rt_capture = 1; wbuf_len = 0; rt_child_fd = fileno(f);
          int64_t c = sweep_run(pid, fn, name, BLOCK_A(next), BLOCK_A(next + 1), sp);
          uint64_t len = wbuf_len;
          rt_write_all(rt_child_fd, &c, 8); rt_write_all(rt_child_fd, &len, 8); rt_write_all(rt_child_fd, wbuf, wbuf_len);
          _exit(0);
        }
        w[next].pid = p; w[next].f = f; running++; next++; sw_to = next;
      }
      while (emit < next && w[emit].done) {
        sweep_take(&w[emit], &count, no, lo, hi, BLOCK_A(emit), BLOCK_A(emit + 1));
        emit++; sw_from = emit;
      }
      if (emit >= nb) break;
      if (emit == next) continue; /* every launched block is in: launch more before waiting */
      int status = 0;
      pid_t p;
      do p = wait(&status); while (p < 0 && errno == EINTR);
      if (p < 0) rt_die("lost track of the sweep workers (%s)", strerror(errno));
      for (int64_t k = emit; k < next; k++)
        if (w[k].pid == p) {
          w[k].done = 1; w[k].status = status; running--;
          if (!WIFEXITED(status) || (WEXITSTATUS(status) != 0 && WEXITSTATUS(status) != 1)) {
            for (int64_t j = emit; j < k; j++) if (!w[j].done) { int st; waitpid(w[j].pid, &st, 0); w[j].done = 1; }
            for (int64_t j = emit; j < k; j++) sweep_take(&w[j], &count, no, lo, hi, BLOCK_A(j), BLOCK_A(j + 1));
            rt_die("a sweep worker crashed (n from %lld to %lld)", (long long)BLOCK_A(k), (long long)(BLOCK_A(k + 1) - 1));
          }
          break;
        }
    }
    (void)first;
    sw_w = NULL;
    free(w);
    i = nb;
  }
#endif
  for (; i < nb; i++) { /* one core, saving each block as it finishes */
    int64_t a = BLOCK_A(i), b = BLOCK_A(i + 1);
    rt_capture = 1; wbuf_len = 0;
    int64_t c = sweep_run(pid, fn, name, a, b, sp);
    rt_capture = 0;
    fwrite(wbuf, 1, wbuf_len, stdout); fflush(stdout);
    save_block(no, lo, hi, a, b, c, wbuf, wbuf_len);
    wbuf_len = 0;
    count += c;
  }
#undef BLOCK_A
  rt_sweep_depth--;
  ENSURE(sp + 1); S[sp] = MKV(count);
  return sp + 1;
}

static void rt_dotpearls(void) {
  int64_t total = 0; char tmp[48];
  for (int i = 0; i < N_PEARL_WORDS; i++) total += shells[i].n;
  int n = snprintf(tmp, sizeof tmp, "(%lld pearls) ", (long long)total); rt_out(tmp, (size_t)n);
}

/* ------------------------------------------------------------------ worlds */

typedef struct { V lo, hi, cur; } Guess;
static Guess *tape = NULL; static long tape_n = 0, tape_cap = 0, tape_pos = 0, sunk = 0;
static jmp_buf world_jmp; static int sink_kind; static V sink_score;

static void rt_sink(int kind, V score) { sink_kind = kind; sink_score = score; longjmp(world_jmp, 1); }
static V rt_wave(V lo, V hi) {
  if (v_cmp(lo, hi) > 0) rt_sink(0, 0);
  if (tape_pos == tape_n) {
    if (tape_n == tape_cap) { tape_cap = tape_cap ? tape_cap * 2 : 64; tape = (Guess *)realloc(tape, sizeof(Guess) * (size_t)tape_cap); }
    tape[tape_n].lo = lo; tape[tape_n].hi = hi; tape[tape_n].cur = lo; tape_n++;
  }
  return tape[tape_pos++].cur;
}

/* ------------------------------------------------------------------ garbage collection */

static V *rt_consts = NULL; static int rt_nconsts = 0;
static void mark(V v) { if (!ISSMALL(v)) { Big *b = BIGP(v); if (!b->mark) b->mark = 1; } }
static V best_score = 0;
static void gc_run(long sp) {
  divc_a = divc_q = NULL;
  for (long i = 0; i < sp; i++) mark(S[i]);
  for (int64_t j = 0; j < (int64_t)bed_size; j++) mark(bed[j]);
  for (int64_t i = 0; i < hb_cap; i++) if (hb[i].full) mark(hb[i].val);
  for (int64_t i = 0; i < echo_cap; i++) if (echoes[i].vals) for (int64_t k = 0; k < echoes[i].len; k++) mark(echoes[i].vals[k]);
  for (int w = 0; w < N_PEARL_WORDS; w++) for (int64_t i = 0; i < shells[w].cap; i++) if (shells[w].t[i].full) { mark(shells[w].t[i].key); mark(shells[w].t[i].val); }
  for (long i = 0; i < tape_n; i++) { mark(tape[i].lo); mark(tape[i].hi); mark(tape[i].cur); }
  mark(best_score);
  Big **p = &big_list; big_bytes = 0;
  while (*p) {
    Big *b = *p;
    if (b->mark) { if (b->mark == 1) b->mark = 0; big_bytes += sizeof(Big) + sizeof(limb) * (size_t)b->n; p = &b->next; }
    else { *p = b->next; free(b); }
  }
  big_next_gc = big_bytes * 2 > ((size_t)64 << 20) ? big_bytes * 2 : ((size_t)64 << 20);
  gc_due = 0;
}
#define GC_POLL(sp) do { if (UNLIKELY(gc_due)) gc_run(sp); } while (0)

/* ------------------------------------------------------------------ program start */

static void rt_reset_world(void) {
  rt_drain(); rt_hush(); wbuf_len = 0; tape_pos = 0;
  for (int i = 0; i < N_PEARL_WORDS; i++) { free(shells[i].t); shells[i].t = NULL; shells[i].cap = shells[i].n = 0; shells[i].on = 0; }
}

static long trawl_main(long sp);
static void trawl_init_consts(void);

static void rt_usage(void) {
  fputs("usage: this program [--jobs N] [--save FILE]\n"
        "  --jobs N    run each top-level `sweep` on N cores (0 = all of them); output stays in order\n"
        "  --save FILE keep finished sweep blocks in FILE; run again with the same FILE to resume\n", stderr);
}
static void rt_args(int argc, char **argv) {
  for (int i = 1; i < argc; i++) {
    const char *a = argv[i], *v = NULL;
    if (!strcmp(a, "--help") || !strcmp(a, "-h")) { rt_usage(); exit(0); }
    if (!strncmp(a, "--jobs=", 7)) v = a + 7;
    else if (!strcmp(a, "--jobs") || !strcmp(a, "-j")) { if (i + 1 >= argc) { rt_usage(); exit(2); } v = argv[++i]; }
    if (v) {
      char *end; long j = strtol(v, &end, 10);
      if (*end || j < 0) { fprintf(stderr, "~ --jobs wants a whole number of cores, not %s\n", v); exit(2); }
#ifndef _WIN32
      if (j == 0) { long c = sysconf(_SC_NPROCESSORS_ONLN); j = c > 0 ? c : 1; }
#else
      if (j != 1) fprintf(stderr, "~ note: --jobs needs fork, which Windows doesn't have, so this runs on one core\n");
      j = 1;
#endif
      rt_jobs = j > 1024 ? 1024 : (int)(j ? j : 1);
      continue;
    }
    if (!strncmp(a, "--save=", 7)) { rt_save_path = a + 7; continue; }
    if (!strcmp(a, "--save")) { if (i + 1 >= argc) { rt_usage(); exit(2); } rt_save_path = argv[++i]; continue; }
    fprintf(stderr, "~ unknown option %s\n", a); rt_usage(); exit(2);
  }
}

int main(int argc, char **argv) {
  static char obuf[1 << 16];
  setvbuf(stdout, obuf, _IOFBF, sizeof obuf);
  rt_args(argc, argv);
  rt_grow(1024); bed_init(BED_INIT);
  trawl_init_consts();
  if (rt_save_path && !rt_world_mode) save_open();
  if (!rt_world_mode) {
    trawl_main(0);
    rt_out("\n", 1);
    rt_flush();
    return 0;
  }
  char *kept = NULL; size_t kept_len = 0; char *best = NULL; size_t best_len = 0; int has_best = 0;
  for (;;) {
    rt_reset_world();
    if (!setjmp(world_jmp)) {
      trawl_main(0);
      fwrite(kept, 1, kept_len, stdout); fwrite(wbuf, 1, wbuf_len, stdout);
      printf("\n(%ld worlds sank along the way)\n", sunk);
      return 0;
    }
    if (sink_kind == 1) { kept = (char *)realloc(kept, kept_len + wbuf_len + 1); memcpy(kept + kept_len, wbuf, wbuf_len); kept_len += wbuf_len; }
    else if (sink_kind == 2 && (!has_best || v_cmp(sink_score, best_score) < 0)) {
      has_best = 1; best_score = sink_score; best = (char *)realloc(best, wbuf_len + 1); memcpy(best, wbuf, wbuf_len); best_len = wbuf_len;
    }
    tape_n = tape_pos;
    while (tape_n && v_eq(tape[tape_n - 1].cur, tape[tape_n - 1].hi) == VTRUE) tape_n--;
    sunk++;
    if (!tape_n) {
      if (has_best || kept_len) {
        fwrite(kept, 1, kept_len, stdout); if (has_best) fwrite(best, 1, best_len, stdout);
        printf("\n(%ld worlds sank along the way)\n", sunk);
        return 0;
      }
      rt_die("every world sank (%ld of them). the sea has no answer.", sunk);
    }
    if (sunk > 2000000) rt_die("2000000 worlds sank and still no shore");
    tape[tape_n - 1].cur = v_add(tape[tape_n - 1].cur, MKV(1));
    GC_POLL(0);
  }
}
#endif
