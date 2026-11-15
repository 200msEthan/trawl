\\ n <= 30000 where 2^n has no digit 0 (OEIS A007377)
for(n = 1, 30000, if(vecmin(digits(2^n)), print1(n, " "))); print();
