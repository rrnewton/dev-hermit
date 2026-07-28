#include <stdio.h>
#include <x86intrin.h>
int main(void){ for(int i=0;i<8;i++) printf("tsc %#llx\n",(unsigned long long)__rdtsc()); return 0; }
