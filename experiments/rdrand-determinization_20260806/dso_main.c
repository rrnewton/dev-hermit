#include <stdio.h>
void lib_rdrand(void);
int main(void) {
    unsigned long long v = 0;
    __asm__ volatile("rdrand %0" : "=r"(v) :: "cc");
    printf("exe-rdrand %016llx\n", v);
    lib_rdrand();
    return 0;
}
