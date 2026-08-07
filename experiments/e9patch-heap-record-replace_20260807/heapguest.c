/* Static, no RDTSC. Grows and reuses the heap so [heap] records are emitted. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void) {
  void *keep[8];
  unsigned long sum = 0;
  for (int i = 0; i < 8; ++i) {
    size_t n = (size_t)(4096 << i);
    keep[i] = malloc(n);
    if (!keep[i]) return 1;
    memset(keep[i], 0x5A + i, n);
    sum += ((unsigned char *)keep[i])[n - 1];
  }
  for (int i = 0; i < 8; i += 2) free(keep[i]);
  for (int i = 0; i < 8; i += 2) { keep[i] = malloc(1024); if (!keep[i]) return 2; memset(keep[i], 7, 1024); }
  printf("heapguest sum=%lu\n", sum);
  return 0;
}
