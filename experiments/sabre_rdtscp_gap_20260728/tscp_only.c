#include <stdio.h>
#include <x86intrin.h>
int main(void){ unsigned a; for(int i=0;i<8;i++) printf("tscp %#llx\n",(unsigned long long)__rdtscp(&a)); return 0; }
