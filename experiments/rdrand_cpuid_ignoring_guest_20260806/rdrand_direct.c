/* Executes RDRAND directly, IGNORING CPUID -- the case masking cannot cover. */
#include <stdio.h>
#include <stdint.h>
static int rdrand64(uint64_t *out) {
    unsigned char ok;
    __asm__ volatile("rdrand %0; setc %1" : "=r"(*out), "=qm"(ok) :: "cc");
    return ok;
}
int main(void) {
    uint64_t v; int ok = 0;
    for (int i = 0; i < 10 && !ok; i++) ok = rdrand64(&v);
    if (!ok) { printf("RDRAND-UNAVAILABLE\n"); return 0; }
    printf("RDRAND=%016llx\n", (unsigned long long)v);
    return 0;
}
