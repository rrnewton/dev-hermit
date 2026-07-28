#include <stdio.h>
#include <time.h>
int main(void){
  struct timespec a,b; clock_gettime(CLOCK_MONOTONIC,&a);
  volatile long x=0; for(long i=0;i<100000;i++) x+=i;
  clock_gettime(CLOCK_MONOTONIC,&b);
  long d=(b.tv_sec-a.tv_sec); /* only coarse seconds, determinized to 0 under hermit */
  printf("elapsed_sec=%ld busy=%ld\n",d,(long)x); return 0;
}
