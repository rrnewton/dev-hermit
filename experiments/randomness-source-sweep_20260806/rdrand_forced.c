#include <stdio.h>
int main(void){
    unsigned long long r=0; unsigned char ok=0;
    __asm__ __volatile__("rdrand %0; setc %1" : "=r"(r), "=qm"(ok));
    printf("forced-rdrand cf=%d %016llx\n", ok, r);
    return 0;
}
