/* Tight sched_getaffinity loop: second zero-kernel Determinized-local probe.
 * detcore threads.rs:1010-1043 services it locally (fixed affinity mask),
 * no kernel injection, no coordinator RPC. Cross-checks the getcpu 3.75x on
 * different handler code. Raw syscall so glibc never short-circuits it. */
#include <stdlib.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <sched.h>

int main(int argc, char **argv) {
    long n = (argc > 1) ? atol(argv[1]) : 100000;
    volatile long acc = 0;
    cpu_set_t mask;
    for (long i = 0; i < n; i++) {
        acc += syscall(SYS_sched_getaffinity, 0, sizeof(mask), &mask);
    }
    return (int)(acc & 1);
}
