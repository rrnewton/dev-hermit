/* Snapshot the heap into .bss BEFORE printing anything, so the probe's own
   stdio buffer -- which lives on the heap -- cannot appear in its own output.
   argv: nthreads lo_hex hi_hex */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
static unsigned long snap[1 << 16];
static void* worker(void* a) { (void)a; return NULL; }
int main(int argc, char** argv) {
  int nthreads = argc > 1 ? atoi(argv[1]) : 4;
  unsigned long lo = strtoul(argv[2], NULL, 16), hi = strtoul(argv[3], NULL, 16);
  pthread_t t[64];
  for (int i = 0; i < nthreads; ++i) if (pthread_create(&t[i], NULL, worker, NULL)) return 1;
  for (int i = 0; i < nthreads; ++i) if (pthread_join(t[i], NULL)) return 2;
  unsigned long n = (hi - lo) / sizeof(unsigned long);
  if (n > (sizeof snap / sizeof *snap)) n = sizeof snap / sizeof *snap;
  const unsigned long* p = (const unsigned long*)lo;
  for (unsigned long i = 0; i < n; ++i) snap[i] = p[i];   /* snapshot first */
  printf("threads=%d heap %lx-%lx words=%lu\n", nthreads, lo, hi, n);
  for (unsigned long i = 0; i < n; ++i)
    if (snap[i]) printf("%lu %lx\n", i * sizeof(unsigned long), snap[i]);
  return 0;
}
