/*
 * Stress family 3: signal delivery ordering.
 *
 * Installs handlers for a set of real-time and standard signals, then:
 *   (a) blocks them all, raises them in a fixed pattern, and unblocks — testing
 *       the kernel/Detcore ordering of pending-signal delivery;
 *   (b) sends a burst of SIGUSR1/SIGUSR2 interleaved with work.
 * Every handler invocation appends its signal number to a delivery log; the
 * order-DEPENDENT digest of that log must be bitwise-stable under --verify.
 *
 * Standard signals do not queue (only the latest pending of each is kept);
 * real-time signals (SIGRTMIN+k) DO queue and preserve send order, so both
 * behaviors are exercised and asserted.
 */
#define _GNU_SOURCE
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define LOGCAP 4096
static volatile sig_atomic_t log_n = 0;
static int deliv_log[LOGCAP];
static volatile sig_atomic_t counts[64];

static void handler(int sig) {
    if (log_n < LOGCAP)
        deliv_log[log_n++] = sig;
    if (sig >= 0 && sig < 64)
        counts[sig]++;
}

static void install(int sig) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = handler;
    sigfillset(&sa.sa_mask); /* serialize handler execution */
    sa.sa_flags = 0;
    sigaction(sig, &sa, NULL);
}

int main(void) {
    int rtmin = SIGRTMIN;
    int std_sigs[] = {SIGUSR1, SIGUSR2};
    int rt_sigs[] = {rtmin, rtmin + 1, rtmin + 2};

    for (unsigned i = 0; i < sizeof std_sigs / sizeof *std_sigs; i++)
        install(std_sigs[i]);
    for (unsigned i = 0; i < sizeof rt_sigs / sizeof *rt_sigs; i++)
        install(rt_sigs[i]);

    /* (a) block all, raise a fixed pattern, then unblock atomically */
    sigset_t block, old;
    sigemptyset(&block);
    for (unsigned i = 0; i < sizeof std_sigs / sizeof *std_sigs; i++)
        sigaddset(&block, std_sigs[i]);
    for (unsigned i = 0; i < sizeof rt_sigs / sizeof *rt_sigs; i++)
        sigaddset(&block, rt_sigs[i]);
    sigprocmask(SIG_BLOCK, &block, &old);

    /* real-time signals queue: send rtmin three times then rtmin+1 twice */
    for (int k = 0; k < 3; k++)
        raise(rt_sigs[0]);
    for (int k = 0; k < 2; k++)
        raise(rt_sigs[1]);
    raise(rt_sigs[2]);
    /* standard signals coalesce: raise USR1 many times -> delivered once */
    for (int k = 0; k < 5; k++)
        raise(SIGUSR1);
    raise(SIGUSR2);

    sigprocmask(SIG_UNBLOCK, &block, NULL); /* delivery happens here */

    /* (b) interleave a burst with a little work */
    uint64_t work = 1469598103934665603ULL;
    for (int i = 0; i < 50; i++) {
        raise(SIGUSR1);
        work ^= (uint64_t)i;
        work *= 1099511628211ULL;
        raise(SIGUSR2);
    }

    /* digest the delivery order */
    uint64_t order = 1469598103934665603ULL;
    for (int i = 0; i < log_n; i++) {
        order ^= (uint64_t)deliv_log[i];
        order *= 1099511628211ULL;
    }

    printf("SIG_RTMIN %d\n", rtmin);
    printf("SIG_TOTAL_DELIVERED %d\n", (int)log_n);
    printf("SIG_RT0_COUNT %d\n", (int)counts[rt_sigs[0]]);
    printf("SIG_RT1_COUNT %d\n", (int)counts[rt_sigs[1]]);
    printf("SIG_RT2_COUNT %d\n", (int)counts[rt_sigs[2]]);
    printf("SIG_USR1_COUNT %d\n", (int)counts[SIGUSR1]);
    printf("SIG_USR2_COUNT %d\n", (int)counts[SIGUSR2]);
    printf("SIG_ORDERDIG %llu\n", (unsigned long long)order);
    printf("SIG_WORK %llu\n", (unsigned long long)work);
    /* RT queueing: rt0 delivered 3x, rt1 2x, rt2 1x if queueing works */
    printf("SIG_RT_QUEUED_OK %d\n",
           counts[rt_sigs[0]] == 3 && counts[rt_sigs[1]] == 2 &&
               counts[rt_sigs[2]] == 1);
    printf("SIG_DONE\n");
    return 0;
}
