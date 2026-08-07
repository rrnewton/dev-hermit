/* Multi-threaded getpid loop: the PARALLEL counterpart to getpid-loop.c.
 *
 * getpid-loop.c is single threaded, so it has no parallelism to lose: running
 * it on 1 core vs many measures INSTRUMENTATION cost only. This guest exists to
 * expose the other half. T threads each issue ITERATIONS/T getpid syscalls, so
 * an uninstrumented runtime can spread them across T cores while Hermit's
 * deterministic scheduler must serialize them. The K=1 vs unconstrained delta
 * on THIS guest is the SEQUENTIALIZATION cost.
 *
 * getpid is used (not compute) so the loop stays syscall-bound and comparable
 * to the blog's getpid microbenchmark.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <unistd.h>

static unsigned long long per_thread;

static void *worker(void *arg) {
  uint64_t sum = 0;
  for (unsigned long long i = 0; i < per_thread; ++i) sum += (uint64_t)syscall(SYS_getpid);
  *(uint64_t *)arg = sum;
  return NULL;
}

int main(int argc, char **argv) {
  if (argc != 3) { fprintf(stderr, "usage: %s ITERATIONS THREADS\n", argv[0]); return 2; }
  unsigned long long iterations = strtoull(argv[1], NULL, 10);
  long threads = strtol(argv[2], NULL, 10);
  if (iterations == 0 || threads <= 0 || threads > 1024) { fprintf(stderr, "bad args\n"); return 2; }
  per_thread = iterations / (unsigned long long)threads;

  pthread_t *tids = calloc((size_t)threads, sizeof *tids);
  uint64_t *sums = calloc((size_t)threads, sizeof *sums);
  if (!tids || !sums) { fprintf(stderr, "oom\n"); return 2; }
  for (long t = 0; t < threads; ++t)
    if (pthread_create(&tids[t], NULL, worker, &sums[t]) != 0) { fprintf(stderr, "pthread_create\n"); return 2; }
  uint64_t total = 0;
  for (long t = 0; t < threads; ++t) { pthread_join(tids[t], NULL); total += sums[t]; }
  /* consume the checksum so the loop cannot be optimized away */
  printf("%llu %ld %llu\n", iterations, threads, (unsigned long long)total);
  return 0;
}
