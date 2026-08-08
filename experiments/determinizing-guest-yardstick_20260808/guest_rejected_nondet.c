/* The REJECTED guest that matters: genuinely load-dependent, not merely
 * miscounted. This is the realistic mistake -- "do work for 2ms" reads like a
 * bounded, reasonable workload, and its syscall count is a function of how
 * fast the machine happens to be running at that instant.
 *
 * Under 20x parallel stress the per-run count moves a lot. A backend compared
 * against this guest would be scored on machine load and the result would be
 * reported as a backend disagreement.
 */
#include <time.h>
#include <stdio.h>
#include <sched.h>
int main(void) {
    struct timespec start, now;
    clock_gettime(CLOCK_MONOTONIC, &start);
    long iters = 0;
    for (;;) {
        sched_yield();                          /* the counted syscall: always traps */
        clock_gettime(CLOCK_MONOTONIC, &now);   /* vDSO: NOT counted, see README */
        iters++;
        long ns = (now.tv_sec - start.tv_sec) * 1000000000L
                + (now.tv_nsec - start.tv_nsec);
        if (ns >= 2000000L) break;              /* 2ms of work */
    }
    fprintf(stderr, "iters=%ld\n", iters);
    return 0;
}
