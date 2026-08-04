/* SYSCALL-BOUND guest: tight loop of a cheap syscall, almost no compute.
 * Uses the raw syscall(2) so glibc does NOT cache the result (getpid() is
 * vDSO/cached in libc) — every iteration actually traps into the kernel and,
 * under hermit, into the supervisor. This is the shape the predecessor tied to
 * LiteInst's 14.5x slowdown: per-syscall host round-trips make hermit's OWN
 * interception/determinization code the hot path.
 *
 * If hermit's own code IS hot here, an UNOPTIMIZED (debug) hermit build should
 * be MATERIALLY slower than an optimized (release) build on this guest — the
 * opposite of the compute-bound guest. That divergence is the finding.
 *
 * Deterministic, single-threaded. Iteration count is a compile-time constant.
 */
#include <stdio.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/syscall.h>

#ifndef ITERS
#define ITERS 3000000ULL   /* each iter = one trapped syscall */
#endif

int main(void) {
    volatile long sink = 0;
    for (uint64_t i = 0; i < ITERS; i++) {
        sink += syscall(SYS_getpid);   /* raw: not libc-cached, always traps */
    }
    printf("%ld\n", (long)sink);       /* consume sink */
    return 0;
}
