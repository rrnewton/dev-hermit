#include <stdio.h>
int main(void){ volatile unsigned long s=0;
  for(unsigned long i=0;i<1500000000UL;i++) s+=i;
  printf("%lu\n", s); return 0; }
