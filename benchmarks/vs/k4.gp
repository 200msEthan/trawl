\\ t < 5000 where n = 8t^4 - 18t^2 + 6 gives ((n+4)!)^2 | (2n)!  (Erdős 727, k = 4)
carries(n, p) = (2*sumdigits(n, p) - sumdigits(2*n, p)) / (p - 1);
ok(n, k) = {
  my(P = Set(concat(vector(k, i, factor(n + i)[,1]~))));
  for(j = 1, #P, my(p = P[j]); if(2 * sum(i = 1, k, valuation(n + i, p)) > carries(n, p), return(0)));
  1;
}
c = 0; for(t = 2, 4999, my(n = 8*t^4 - 18*t^2 + 6); if(ok(n, 4), print1(t, " "); c++)); print("count ", c);
