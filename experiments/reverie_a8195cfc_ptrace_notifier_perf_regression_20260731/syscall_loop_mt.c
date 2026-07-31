// Multi-threaded syscall-loop microbench: T threads each do N getpid() calls.
// Exercises the reverie-ptrace notifier worker/poller status handoff under
// cross-thread contention (the a8195cfc regression is contention-driven).
#define _GNU_SOURCE
#include <pthread.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <stdio.h>
#include <stdlib.h>
static long per_thread;
static void* worker(void* _) {
    volatile long acc = 0;
    for (long i = 0; i < per_thread; i++) acc += syscall(SYS_getpid);
    return (void*)(long)acc;
}
int main(int argc, char** argv) {
    int T = argc > 1 ? atoi(argv[1]) : 4;
    per_thread = argc > 2 ? atol(argv[2]) : 50000;
    pthread_t th[64];
    for (int i = 0; i < T; i++) pthread_create(&th[i], NULL, worker, NULL);
    for (int i = 0; i < T; i++) pthread_join(th[i], NULL);
    printf("done %d threads x %ld getpid = %ld syscalls\n", T, per_thread, (long)T*per_thread);
    return 0;
}
