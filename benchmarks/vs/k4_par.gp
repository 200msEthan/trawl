\\ The k = 4 family for t < 60000 on every core: parvector shares the t values out, like Trawl's sweep.
carries(n, p) = (2*sumdigits(n, p) - sumdigits(2*n, p)) / (p - 1);
ok(n, k) = {
  my(P = Set(concat(vector(k, i, factor(n + i)[,1]~))));
  for(j = 1, #P, my(p = P[j]); if(2 * sum(i = 1, k, valuation(n + i, p)) > carries(n, p), return(0)));
  1;
}
export(carries, ok);
v = parvector(59998, i, my(t = i + 1); ok(8*t^4 - 18*t^2 + 6, 4));
c = 0; for(i = 1, #v, if(v[i], print1(i + 1, " "); c++)); print("count ", c);
