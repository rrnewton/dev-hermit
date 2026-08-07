/* Identical to heap_const EXCEPT it stores an absolute address in the heap. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void) {
  size_t n = 1 << 16;
  unsigned char* b = malloc(n);
  if (!b) return 1;
  memset(b, 0xAB, n);
  void* self = b;
  memcpy(b, &self, sizeof self);          /* the ONLY difference */
  unsigned long s = 0;
  for (size_t i = sizeof self; i < n; ++i) s += b[i];
  printf("ptr sum=%lu\n", s);             /* sum skips the pointer: stdout stays stable */
  return 0;
}
