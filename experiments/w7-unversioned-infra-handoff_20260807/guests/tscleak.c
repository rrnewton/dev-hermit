/* Puts a raw TSC into live stack memory, then makes syscalls so the stack is
   hashed while the value is still resident. If the backend does not virtualise
   RDTSC, this value differs every run and the stack hash must differ with it. */
#include <stdio.h>
#include <stdint.h>
#include <unistd.h>
static inline uint64_t rdtsc(void) {
    uint32_t lo, hi;
    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi) :: "memory");
    return ((uint64_t)hi << 32) | lo;
}
int main(void) {
    volatile uint64_t stamps[64];
    for (int i = 0; i < 64; ++i) stamps[i] = rdtsc();
    for (int i = 0; i < 8; ++i) { (void)getpid(); }   /* syscalls -> hash points */
    uint64_t acc = 0;
    for (int i = 0; i < 64; ++i) acc ^= stamps[i];
    /* Print only whether it is nonzero, so stdout stays deterministic. */
    printf("tsc_captured=%d\n", acc != 0);
    return 0;
}
