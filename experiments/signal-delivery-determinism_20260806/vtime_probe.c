/* How much VIRTUAL time elapses over the same 200k-getpid loop? If < 1ms, a 1ms
   timer legitimately never becomes due and the 0-ticks result is NOT a bug. */
#define _GNU_SOURCE
#include <stdio.h>
#include <time.h>
#include <unistd.h>
int main(void){ struct timespec a,b; clock_gettime(CLOCK_REALTIME,&a);
  for(long i=0;i<200000;i++)(void)getpid();
  clock_gettime(CLOCK_REALTIME,&b);
  double ms=(b.tv_sec-a.tv_sec)*1000.0+(b.tv_nsec-a.tv_nsec)/1e6;
  printf("elapsed_ms=%.3f\n",ms); return 0;}
