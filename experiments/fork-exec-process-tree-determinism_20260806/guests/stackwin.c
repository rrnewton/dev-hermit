/* Dump a WIDE window of the guest's own stack around SP, before and after a
   sysinfo(2) call, so the [stack]-hash divergence can be attributed to actual
   byte offsets instead of inferred. Deliberately reads bytes the program never
   wrote -- that residue is precisely what --detlog-stack hashes. */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/sysinfo.h>

#define WIN 4096

static void dump(const char *tag, volatile unsigned char *base) {
  /* print a checksum per 64-byte line so the output stays small but any
     differing region is localized */
  for (int off = -WIN; off < WIN; off += 64) {
    unsigned long h = 1469598103934665603UL;
    for (int i = 0; i < 64; i++) { h ^= base[off + i]; h *= 1099511628211UL; }
    printf("%s %+6d %016lx\n", tag, off, h);
  }
}

int main(int argc, char **argv) {
  volatile unsigned char anchor[16];
  struct sysinfo si;
  memset(&si, 0, sizeof si);
  dump("pre ", anchor);
  if (argc == 1) sysinfo(&si);
  dump("post", anchor);
  return 0;
}
