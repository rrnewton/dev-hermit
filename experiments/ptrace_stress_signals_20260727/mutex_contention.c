/*
 * Stress family 1: multi-threaded mutex + condvar contention.
 *
 * NTHREAD workers hammer a single shared counter under one mutex (heavy lock
 * contention), and a condvar ping-pong bounces a token between two threads
 * TOKENS times. Both an order-INDEPENDENT total and an order-DEPENDENT digest
 * of the lock-acquisition sequence are printed, so a nondeterministic scheduler
 * shows up as a --verify divergence, not just a silently-different total.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define NTHREAD 8
#define ITERS 2000
#define TOKENS 200

static pthread_mutex_t mx = PTHREAD_MUTEX_INITIALIZER;
static uint64_t counter = 0;
static uint64_t acq_digest = 1469598103934665603ULL; /* FNV over acquiring tid */
static long acquisitions = 0;

static void *worker(void *arg) {
    long id = (long)arg;
    for (int i = 0; i < ITERS; i++) {
        pthread_mutex_lock(&mx);
        counter += (uint64_t)(id + 1);
        acq_digest ^= (uint64_t)(id + 1);
        acq_digest *= 1099511628211ULL;
        acquisitions++;
        pthread_mutex_unlock(&mx);
    }
    return NULL;
}

/* condvar ping-pong between two threads */
static pthread_mutex_t pm = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t pc = PTHREAD_COND_INITIALIZER;
static int turn = 0; /* 0 => A's turn, 1 => B's turn */
static int pp_count = 0;

static void *ping(void *arg) {
    int me = (int)(long)arg;
    for (int i = 0; i < TOKENS; i++) {
        pthread_mutex_lock(&pm);
        while (turn != me)
            pthread_cond_wait(&pc, &pm);
        pp_count++;
        turn = 1 - me;
        pthread_cond_broadcast(&pc);
        pthread_mutex_unlock(&pm);
    }
    return NULL;
}

int main(void) {
    pthread_t t[NTHREAD];
    for (long i = 0; i < NTHREAD; i++)
        pthread_create(&t[i], NULL, worker, (void *)i);
    for (int i = 0; i < NTHREAD; i++)
        pthread_join(t[i], NULL);

    pthread_t a, b;
    pthread_create(&a, NULL, ping, (void *)0L);
    pthread_create(&b, NULL, ping, (void *)1L);
    pthread_join(a, NULL);
    pthread_join(b, NULL);

    uint64_t expected = 0;
    for (long i = 0; i < NTHREAD; i++)
        expected += (uint64_t)(i + 1) * ITERS;

    printf("MUTEX_THREADS %d\n", NTHREAD);
    printf("MUTEX_ACQUISITIONS %ld\n", acquisitions);
    printf("MUTEX_COUNTER %llu\n", (unsigned long long)counter);
    printf("MUTEX_EXPECTED %llu\n", (unsigned long long)expected);
    printf("MUTEX_ACQDIGEST %llu\n", (unsigned long long)acq_digest);
    printf("MUTEX_PINGPONG %d\n", pp_count);
    printf("MUTEX_OK %d\n", counter == expected && pp_count == 2 * TOKENS);
    printf("MUTEX_DONE\n");
    return 0;
}
