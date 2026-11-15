// Erdos #727 / OEIS A343507: least n with ((n+k)!)^2 | (2n)!, i.e. ((n+1)...(n+k))^2 | C(2n,n).
// Kummer: v_p(C(2n,n)) = carries when adding n+n in base p. A prime p > sqrt(2n) gives at most 1 carry,
// so it may not divide any of n+1..n+k: those k numbers must all be sqrt(2n)-smooth.
// Log sieve for runs of k smooth numbers, then exact check.  usage: e727 KMIN KMAX START END
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <math.h>
typedef unsigned long long u64;
static uint32_t *P; static long NP;
static int carries(u64 n,u64 p){int c=0,cr=0;while(n||cr){u64 d=n%p;cr=(2*d+cr)>=p;c+=cr;n/=p;}return c;}
static u64 isq(u64 x){u64 r=sqrtl((long double)x);while(r*r>x)r--;while((r+1)*(r+1)<=x)r++;return r;}
static long cands=0, smallpass=0;
static int full(u64 n,int k){
  // fast small-prime filter
  static const int sp[]={2,3,5,7,11,13,17,19,23,29,31,37};
  for(int t=0;t<12;t++){u64 p=sp[t];int v=0;for(int i=1;i<=k;i++){u64 x=n+i;while(x%p==0){x/=p;v++;}} if(2*v>carries(n,p)) return 0;}
  smallpass++;
  u64 rem[64]; for(int i=1;i<=k;i++){rem[i]=n+i; for(int t=0;t<12;t++) while(rem[i]%sp[t]==0) rem[i]/=sp[t];}
  u64 B=isq(2*n);
  for(long t=12;t<NP && P[t]<=B;t++){u64 p=P[t];int v=0;
    for(int i=1;i<=k;i++){ if(rem[i]%p==0){ do{rem[i]/=p;v++;}while(rem[i]%p==0);} }
    if(v && 2*v>carries(n,p)) return 0;
    int done=1; for(int i=1;i<=k;i++) if(rem[i]!=1){done=0;break;} if(done) return 1;
  }
  for(int i=1;i<=k;i++) if(rem[i]!=1) return 0; // a prime > sqrt(2n)
  return 1;
}
int main(int argc,char**argv){
  int K0=atoi(argv[1]),K1=atoi(argv[2]); u64 START=strtoull(argv[3],0,10), END=strtoull(argv[4],0,10);
  u64 R=isq(2*END+2*(1ULL<<24))+2;
  uint8_t *b=calloc(R+1,1); P=malloc(sizeof(uint32_t)*(R/2+100)); NP=0;
  for(u64 i=2;i<=R;i++) if(!b[i]){ P[NP++]=i; for(u64 j=i*i;j<=R;j+=i) b[j]=1; }
  uint8_t *lp=malloc(NP); for(long t=0;t<NP;t++) lp[t]=(uint8_t)floor(4.0*log2((double)P[t]));
  u64 W=1ULL<<24; uint8_t *s=malloc(W); u64 found[64]={0}; int run=0;
  for(u64 lo=START+1; lo<=END+64; lo+=W){
    int all=1; for(int k=K0;k<=K1;k++) if(!found[k]) all=0; if(all) break;
    u64 hi=lo+W; u64 B=isq(2*hi)+1;
    for(u64 i=0;i<W;i++) s[i]=0;
    for(long t=0;t<NP && P[t]<=B;t++){ u64 p=P[t]; uint8_t l=lp[t];
      for(u64 q=p; q<hi; q*=p){ u64 st=((lo+q-1)/q)*q; for(u64 x=st;x<hi;x+=q) s[x-lo]+=l; if(q>hi/p) break; } }
    int thr=(int)floor(4.0*log2((double)lo)) - 48; // slack 12 bits; true large cofactor >= B >> 2^12
    for(u64 i=0;i<W;i++){
      if(s[i]>=thr) run++; else run=0;
      u64 x=lo+i; // x = n + k is the top of the window
      for(int k=K0;k<=K1;k++){ if(found[k]) continue; if(run<k) break;
        u64 n=x-k; if(n<=START) continue; cands++;
        if(full(n,k)){ found[k]=n; printf("k=%d n=%llu\n",k,n); fflush(stdout);} }
    }
    if(((lo-START-1)/W)%64==0) {fprintf(stderr,"done to %llu cands=%ld smallpass=%ld\n",hi,cands,smallpass);}
  }
  for(int k=K0;k<=K1;k++) if(!found[k]) printf("k=%d none in (%llu, %llu]\n",k,START,END);
  fprintf(stderr,"cands=%ld smallpass=%ld\n",cands,smallpass);
}
