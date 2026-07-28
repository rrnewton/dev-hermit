// Hard signal-handling determinism probe.
// Exercises: SA_SIGINFO handlers, real-time signal queueing/ordering via
// sigqueue, sigprocmask block/pending/unblock, nested delivery, and
// deterministic accumulation. Output must be identical across hermit runs.
#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static volatile sig_atomic_t usr1_count = 0;
static volatile sig_atomic_t usr2_count = 0;
static volatile long rt_sum = 0;          // sum of queued RT signal values
static volatile sig_atomic_t rt_count = 0;
static volatile sig_atomic_t nested_seen = 0;

static void usr_handler(int sig, siginfo_t *si, void *uc) {
    (void)uc;
    if (sig == SIGUSR1) {
        usr1_count++;
        // Nested: while handling USR1, raise USR2 (unblocked) once.
        if (usr1_count == 1) {
            nested_seen = 1;
            raise(SIGUSR2);
        }
    } else if (sig == SIGUSR2) {
        usr2_count++;
    }
    (void)si;
}

static void rt_handler(int sig, siginfo_t *si, void *uc) {
    (void)sig; (void)uc;
    rt_count++;
    rt_sum += si->si_value.sival_int;
}

int main(void) {
    struct sigaction sa;

    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = usr_handler;
    sa.sa_flags = SA_SIGINFO;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGUSR1, &sa, NULL);
    sigaction(SIGUSR2, &sa, NULL);

    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = rt_handler;
    sa.sa_flags = SA_SIGINFO;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGRTMIN, &sa, NULL);

    // Part A: direct raises with nested delivery.
    for (int i = 0; i < 5; i++) raise(SIGUSR1);

    // Part B: block SIGRTMIN, queue N values, verify they are pending,
    // then unblock and let them all deliver in FIFO order (RT semantics).
    sigset_t block, oldset, pending;
    sigemptyset(&block);
    sigaddset(&block, SIGRTMIN);
    sigprocmask(SIG_BLOCK, &block, &oldset);

    long expect_sum = 0;
    for (int v = 1; v <= 10; v++) {
        union sigval sv; sv.sival_int = v;
        sigqueue(getpid(), SIGRTMIN, sv);
        expect_sum += v;
    }

    sigpending(&pending);
    int pend_rt = sigismember(&pending, SIGRTMIN) ? 1 : 0;

    sigprocmask(SIG_UNBLOCK, &block, NULL);  // flush the queue

    // Give delivery a chance (all pending should drain synchronously here).
    for (int spin = 0; spin < 1000000 && rt_count < 10; spin++) { /* busy */ }

    printf("usr1=%d\n", usr1_count);
    printf("usr2=%d\n", usr2_count);
    printf("nested_seen=%d\n", nested_seen);
    printf("pending_rt_was_set=%d\n", pend_rt);
    printf("rt_count=%d\n", rt_count);
    printf("rt_sum=%ld expect=%ld match=%d\n", rt_sum, expect_sum, rt_sum == expect_sum);
    return 0;
}
