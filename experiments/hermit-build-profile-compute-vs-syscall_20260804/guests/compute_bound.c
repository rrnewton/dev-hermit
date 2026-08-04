/* COMPUTE-BOUND guest: heavy CPU arithmetic, almost no syscalls.
 * The guest itself does the work; hermit's supervisor is barely on the hot
 * path. If "hermit doesn't do much compute" holds, an UNOPTIMIZED (debug)
 * hermit build should run this ~as fast as an optimized (release) build,
 * because hermit's own code executes rarely relative to guest instructions.
 *
 * Deterministic, single-threaded, N passes over an integer-mix kernel.
 * Only syscalls: process startup + one final write(). Iteration count is a
 * compile-time constant so both profiles run identical work.
 */
#include <stdio.h>
#include <stdint.h>

#ifndef ITERS
#define ITERS 800000000ULL   /* ~1-3s native; tune so it is compute-dominated */
#endif

int main(void) {
    uint64_t acc = 1469598103934665603ULL; /* FNV offset basis */
    for (uint64_t i = 0; i < ITERS; i++) {
        acc ^= i;
        acc *= 1099511628211ULL;            /* FNV prime */
        acc = (acc << 13) | (acc >> 51);     /* rotate to defeat strength-reduction */
        acc += i * 2654435761ULL;
    }
    /* single syscall: consume acc so the loop is not optimized away */
    printf("%llu\n", (unsigned long long)acc);
    return 0;
}
