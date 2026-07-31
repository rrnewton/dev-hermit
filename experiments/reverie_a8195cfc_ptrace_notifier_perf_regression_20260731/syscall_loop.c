// Tight syscall loop microbench: N getpid() calls. Measures per-syscall
// ptrace-stop overhead in the reverie supervisor (getpid is cheap in-guest,
// so wall-time and supervisor ptrace count are dominated by per-stop cost).
#include <unistd.h>
#include <sys/syscall.h>
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char** argv) {
    long n = argc > 1 ? atol(argv[1]) : 200000;
    volatile long acc = 0;
    for (long i = 0; i < n; i++) acc += syscall(SYS_getpid);
    printf("did %ld getpid syscalls acc=%ld\n", n, acc);
    return 0;
}
