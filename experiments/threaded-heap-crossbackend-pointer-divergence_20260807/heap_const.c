/* Heap content with NO stored pointers. Control for the pointer hypothesis. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void) {
  size_t n = 1 << 16;
  unsigned char* b = malloc(n);
  if (!b) return 1;
  memset(b, 0xAB, n);
  unsigned long s = 0;
  for (size_t i = 0; i < n; ++i) s += b[i];
  printf("const sum=%lu\n", s);
  return 0;
}
