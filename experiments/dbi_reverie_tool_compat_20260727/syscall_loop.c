/* Fixed-count getpid() microbenchmark guest: forces N real syscalls. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/syscall.h>
int main(int argc, char **argv) {
    long n = (argc > 1) ? atol(argv[1]) : 200000;
    long pid = 0;
    for (long i = 0; i < n; i++) pid = syscall(SYS_getpid);
    printf("SYSCALL-LOOP done n=%ld pid=%ld\n", n, pid);
    return 0;
}
