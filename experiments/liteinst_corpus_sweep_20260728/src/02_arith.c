#include <stdio.h>
int main(void){ long s=0; for(long i=1;i<=1000000;i++) s+=i%7; printf("sum=%ld\n",s); return 0; }
