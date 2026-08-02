/* Tight getpid loop: isolates per-syscall interception cost.
 * getpid is never cached by glibc's PID cache path we care about here because
 * we call the raw syscall directly, so every iteration crosses the backend. */
#include <stdlib.h>
#include <unistd.h>
#include <sys/syscall.h>

int main(int argc, char **argv) {
    long n = (argc > 1) ? atol(argv[1]) : 100000;
    volatile long acc = 0;
    for (long i = 0; i < n; i++) {
        acc += syscall(SYS_getpid);
    }
    return (int)(acc & 1);
}
