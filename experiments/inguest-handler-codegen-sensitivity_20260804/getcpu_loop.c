/* Tight getcpu loop: 100%-handler probe with NO kernel round-trip.
 * getcpu is Determinized locally in detcore (misc.rs: writes cpu=0/node=0,
 * returns Ok(0)) — no kernel injection, no coordinator RPC. Contrast getpid,
 * which is PassThrough and injects a real kernel getpid (~74ns floor). So the
 * getcpu A(opt3) vs D(opt0) ratio is pure user-space handler codegen with the
 * kernel floor removed. Raw syscall so glibc never short-circuits it. */
#include <stdlib.h>
#include <unistd.h>
#include <sys/syscall.h>

int main(int argc, char **argv) {
    long n = (argc > 1) ? atol(argv[1]) : 100000;
    volatile long acc = 0;
    unsigned cpu = 0, node = 0;
    for (long i = 0; i < n; i++) {
        acc += syscall(SYS_getcpu, &cpu, &node, (void*)0);
        acc += cpu;
    }
    return (int)(acc & 1);
}
