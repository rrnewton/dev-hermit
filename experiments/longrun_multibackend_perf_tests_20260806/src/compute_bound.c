/* Compute-dense CONTROL: same runtime class, ~1000x fewer syscalls.
 * ptrace taxes syscalls, not compute -- so a backend ranking that shows a
 * large spread here is measuring something other than interception cost.
 * Single-process, single-thread, deterministic. */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#define N (1u<<22)
static uint32_t st[N];
int main(void) {
    uint64_t h = 1469598103934665603ULL;
    for (unsigned i = 0; i < N; i++) st[i] = i * 2654435761u;
    for (int pass = 0; pass < 800; pass++) {
        for (unsigned i = 1; i < N; i++) st[i] ^= (st[i-1] >> 3) * 0x9E3779B1u;
        h ^= st[N-1]; h *= 1099511628211ULL;
    }
    printf("compute_bound ok h=%llu\n", (unsigned long long)h);
    return 0;
}
