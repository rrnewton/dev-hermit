// Bounded-queue producer/consumer determinism probe.
// Distinct from a spin-contention test: blocking mutex + two condvars,
// multiple producers and consumers, fixed total work, checksum of consumed
// payloads. hermit must serialize the wakeups deterministically.
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

#define CAP 8
#define NPROD 4
#define NCONS 3
#define PER_PROD 500          // items per producer
#define TOTAL (NPROD * PER_PROD)

static int buf[CAP];
static int head, tail, count;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t not_full = PTHREAD_COND_INITIALIZER;
static pthread_cond_t not_empty = PTHREAD_COND_INITIALIZER;

static int produced_total, consumed_total;
static uint64_t consumed_sum;   // order-independent checksum

static void *producer(void *arg) {
    long id = (long)arg;
    for (int i = 0; i < PER_PROD; i++) {
        int item = (int)(id * 100000 + i);
        pthread_mutex_lock(&mu);
        while (count == CAP) pthread_cond_wait(&not_full, &mu);
        buf[tail] = item; tail = (tail + 1) % CAP; count++;
        produced_total++;
        pthread_cond_signal(&not_empty);
        pthread_mutex_unlock(&mu);
    }
    return NULL;
}

static void *consumer(void *arg) {
    (void)arg;
    for (;;) {
        pthread_mutex_lock(&mu);
        while (count == 0 && consumed_total < TOTAL)
            pthread_cond_wait(&not_empty, &mu);
        if (count == 0 && consumed_total >= TOTAL) {
            pthread_mutex_unlock(&mu);
            break;
        }
        int item = buf[head]; head = (head + 1) % CAP; count--;
        consumed_total++;
        consumed_sum += (uint64_t)(uint32_t)item;
        pthread_cond_signal(&not_full);
        // Wake other consumers so they can observe termination.
        pthread_cond_broadcast(&not_empty);
        pthread_mutex_unlock(&mu);
    }
    return NULL;
}

int main(void) {
    pthread_t p[NPROD], c[NCONS];
    for (long i = 0; i < NPROD; i++) pthread_create(&p[i], NULL, producer, (void *)i);
    for (long i = 0; i < NCONS; i++) pthread_create(&c[i], NULL, consumer, (void *)i);
    for (int i = 0; i < NPROD; i++) pthread_join(p[i], NULL);
    // All produced: wake any waiting consumers to let them finish/terminate.
    pthread_mutex_lock(&mu);
    pthread_cond_broadcast(&not_empty);
    pthread_mutex_unlock(&mu);
    for (int i = 0; i < NCONS; i++) pthread_join(c[i], NULL);

    // Expected checksum computed independently.
    uint64_t expect = 0;
    for (long id = 0; id < NPROD; id++)
        for (int i = 0; i < PER_PROD; i++)
            expect += (uint64_t)(uint32_t)(int)(id * 100000 + i);

    printf("produced=%d\n", produced_total);
    printf("consumed=%d\n", consumed_total);
    printf("consumed_sum=%llu expect=%llu match=%d\n",
           (unsigned long long)consumed_sum, (unsigned long long)expect,
           consumed_sum == expect);
    return 0;
}
