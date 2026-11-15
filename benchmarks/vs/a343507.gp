\\ A343507 search, same algorithm as the Trawl and C versions: log sieve, then an exact check.
carries(n,p) = (2*sumdigits(n,p) - sumdigits(2*n,p)) / (p-1);
full(n,m) = {
  my(B = sqrtint(2*n));
  for(i=1, m, my(f = factor(n+i));
    for(j=1, #f~, my(p = f[j,1]); if(p > B, return(0));
      my(v = sum(r=1, m, valuation(n+r, p)));
      if(2*v > carries(n,p), return(0))));
  1;
}
search(m, start, stop) = {
  my(W = 2^20, R = sqrtint(2*stop + 2*W) + 2, P = primes([2, R]), L = vectorsmall(#P, k, logint(P[k]^4, 2)));
  my(s = vectorsmall(W), run = 0, lo = start + 1);
  while(lo <= stop + 64,
    my(hi = lo + W, B = sqrtint(2*hi) + 1);
    for(i=1, W, s[i] = 0);
    for(k=1, #P, my(p = P[k]); if(p > B, break); my(q = p, l = L[k]);
      while(1, forstep(x = ceil(lo/q)*q, hi-1, q, s[x-lo+1] += l); if(q > hi\p, break); q *= p));
    my(thr = logint(lo^4, 2) - 48);
    for(i=1, W, if(s[i] >= thr, run++, run = 0);
      if(run >= m, my(n = lo + i - 1 - m); if(n > start && full(n, m), return(n))));
    lo += W);
  0;
}
