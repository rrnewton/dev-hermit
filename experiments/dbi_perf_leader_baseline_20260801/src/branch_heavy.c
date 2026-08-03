/* branch_heavy: a syscall-free, branch-dense workload.
 *
 * Sums Collatz step counts over 1..limit. The inner loop is a tight
 * data-dependent branch (n & 1) plus a loop back-edge, so it retires a very
 * large number of *counted branches* (cbr/ubr) with essentially no syscalls.
 * This isolates the DBI backend's per-branch instrumentation cost from its
 * per-syscall interception cost.
 *
 * Usage: branch_heavy [limit]   (default 2000000)
 */
#include <stdio.h>
#include <stdlib.h>

static unsigned long collatz_steps(unsigned long n) {
    unsigned long steps = 0;
    while (n > 1) {
        if (n & 1UL)
            n = 3UL * n + 1UL;
        else
            n >>= 1;
        steps++;
    }
    return steps;
}

int main(int argc, char **argv) {
    unsigned long limit = 2000000UL;
    if (argc > 1)
        limit = strtoul(argv[1], NULL, 10);

    unsigned long total = 0;
    for (unsigned long i = 1; i <= limit; i++)
        total += collatz_steps(i);

    printf("collatz total steps 1..%lu = %lu\n", limit, total);
    return 0;
}
