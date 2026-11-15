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

static void rt_die(const char *fmt, ...) {
  va_list ap;
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
  V q; big_divmod(a, b, &q, NULL); return q;
}
static inline V v_mod(V a, V b) {
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
  V total = 0; int g = 1;
  while (x != 0) {
    V q, r; big_divmod(x, b, &q, &r);
    total = g > 0 ? v_add(total, r) : v_sub(total, r);
    x = q; if (alternate) g = -g;
  }
  return total;
}

/* ------------------------------------------------------------------ output */

static char *wbuf = NULL; static size_t wbuf_len = 0, wbuf_cap = 0; /* this world's words */

static void rt_out(const char *s, size_t n) {
  if (rt_world_mode) {
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

int main(void) {
  static char obuf[1 << 16];
  setvbuf(stdout, obuf, _IOFBF, sizeof obuf);
  rt_grow(1024); bed_init(BED_INIT);
  trawl_init_consts();
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
