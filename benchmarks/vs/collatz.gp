\\ Collatz records below 10^7 (OEIS A006877)
best = -1;
for(n = 1, 10^7 - 1, my(x = n, s = 0); while(x > 1, if(x % 2, x = 3*x + 1, x >>= 1); s++); if(s > best, best = s; print1(n, " ")));
print();
