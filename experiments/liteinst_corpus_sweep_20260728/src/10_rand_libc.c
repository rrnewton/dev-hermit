#include <stdio.h>
#include <stdlib.h>
int main(void){ srand(12345); long s=0; for(int i=0;i<20;i++) s+=rand()%100; printf("s=%ld\n",s); return 0; }
