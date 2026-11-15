"""The same A343507 search as examples/a343507.trawl, in plain Python, for timing.

usage: python a343507.py M START END   (prints the least n in (START, END], or 0)
"""
import math
import sys


def carries(n, p):
    c = cr = 0
    while n or cr:
        cr = 1 if 2 * (n % p) + cr >= p else 0
        c += cr
        n //= p
    return c


def search(m, start, end, W=1 << 20):
    R = math.isqrt(2 * end + 2 * W) + 2
    comp = bytearray(R + 1)
    primes = []
    for i in range(2, R + 1):
        if not comp[i]:
            primes.append(i)
            for j in range(i * i, R + 1, i):
                comp[j] = 1
    logs = [(p ** 4).bit_length() - 1 for p in primes]

    def full(n):
        rem = [n + i for i in range(1, m + 1)]
        B = math.isqrt(2 * n)
        for p in primes:
            if p > B:
                break
            v = 0
            for i in range(m):
                while rem[i] % p == 0:
                    rem[i] //= p
                    v += 1
            if v and 2 * v > carries(n, p):
                return False
            if all(r == 1 for r in rem):
                return True
        return all(r == 1 for r in rem)

    run, lo = 0, start + 1
    while lo <= end + 64:
        hi = lo + W
        B = math.isqrt(2 * hi) + 1
        s = [0] * W
        for p, l in zip(primes, logs):
            if p > B:
                break
            q = p
            while True:
                for x in range(-(-lo // q) * q, hi, q):
                    s[x - lo] += l
                if q > hi // p:
                    break
                q *= p
        thr = (lo ** 4).bit_length() - 1 - 48
        for i in range(W):
            run = run + 1 if s[i] >= thr else 0
            if run >= m:
                n = lo + i - m
                if n > start and full(n):
                    return n
        lo += W
    return 0


if __name__ == "__main__":
    print(search(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])))
