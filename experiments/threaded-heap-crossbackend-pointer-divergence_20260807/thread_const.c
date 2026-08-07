/* Threads, but NO pointer ever stored in the heap. Isolates threading itself. */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
enum { THREADS = 4, N = 1 << 14 };
static unsigned char* buf;
static void* worker(void* a) {
  int k = *(int*)a;
  memset(buf + (size_t)k * N, 0xC0 + k, N);
  return NULL;
}
int main(void) {
  pthread_t t[THREADS]; int id[THREADS];
  buf = malloc((size_t)THREADS * N);
  if (!buf) return 1;
  for (int i = 0; i < THREADS; ++i) { id[i] = i; if (pthread_create(&t[i], NULL, worker, &id[i])) return 1; }
  for (int i = 0; i < THREADS; ++i) if (pthread_join(t[i], NULL)) return 2;
  unsigned long s = 0;
  for (size_t i = 0; i < (size_t)THREADS * N; ++i) s += buf[i];
  printf("thread_const sum=%lu\n", s);
  return 0;
}
