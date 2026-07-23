/* CPU-bound loop, ~10s native. */
#include <stdio.h>
int main(void){ volatile unsigned long s=0;
  for(unsigned long i=0;i<29000000000UL;i++) s+=i;
  printf("%lu\n", s); return 0; }
