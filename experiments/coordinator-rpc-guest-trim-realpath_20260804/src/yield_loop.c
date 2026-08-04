/* Coordinator-hitting probe: sched_yield is a scheduling event -> Detcore
 * resource_request (hermit/detcore/src/lib.rs:703). Contrast with getpid
 * (PassThrough, no RPC). argv[1] = iteration count. */
#include <stdlib.h>
#include <sched.h>
int main(int argc, char **argv) {
    long n = (argc > 1) ? atol(argv[1]) : 100000;
    for (long i = 0; i < n; i++) sched_yield();
    return 0;
}
