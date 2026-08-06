// RDRAND inside a shared library: covered only if the mmap hook works.
#include <stdio.h>
void lib_rdrand(void) {
    for (int i = 0; i < 2; i++) {
        unsigned long long v = 0; unsigned char cf = 0;
        __asm__ volatile("rdrand %0; setc %1" : "=r"(v), "=qm"(cf) :: "cc");
        printf("dso-rdrand[%d] cf=%u %016llx\n", i, cf, v);
    }
}
