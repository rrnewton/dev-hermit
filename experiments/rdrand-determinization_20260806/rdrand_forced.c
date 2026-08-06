// Executes RDRAND/RDSEED unconditionally, WITHOUT consulting CPUID.
#include <stdio.h>
int main(void) {
    for (int i = 0; i < 4; i++) {
        unsigned long long v = 0; unsigned char cf = 0;
        __asm__ volatile("rdrand %0; setc %1" : "=r"(v), "=qm"(cf) :: "cc");
        printf("rdrand[%d] cf=%u %016llx\n", i, cf, v);
    }
    for (int i = 0; i < 2; i++) {
        unsigned int v32 = 0; unsigned char cf = 0;
        __asm__ volatile("rdrand %0; setc %1" : "=r"(v32), "=qm"(cf) :: "cc");
        printf("rdrand32[%d] cf=%u %08x\n", i, cf, v32);
    }
    for (int i = 0; i < 2; i++) {
        unsigned long long v = 0; unsigned char cf = 0;
        __asm__ volatile("rdseed %0; setc %1" : "=r"(v), "=qm"(cf) :: "cc");
        printf("rdseed[%d] cf=%u %016llx\n", i, cf, v);
    }
    return 0;
}
