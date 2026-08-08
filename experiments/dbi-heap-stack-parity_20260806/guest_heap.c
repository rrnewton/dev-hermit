/* Exercises the brk heap: many small mallocs stay under glibc's 128KiB
   M_MMAP_THRESHOLD, so they come from brk and land in the [heap] mapping
   that detcore's --detlog-heap actually hashes. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void) {
  char *p[2000];
  for (int i = 0; i < 2000; i++) { p[i] = malloc(64); memset(p[i], (i & 0x7f), 64); }
  unsigned long s = 0;
  for (int i = 0; i < 2000; i++) s += (unsigned char)p[i][0];
  printf("sum=%lu\n", s);
  return 0;
}
