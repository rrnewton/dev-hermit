/* REFERENCE GUEST for the STDOUT dimension.
 *
 * The stdout dimension compares the guest's own bytes. A guest qualifies only
 * if it WRITES a nontrivial, deterministic byte sequence of its own making --
 * `/bin/true` writes zero bytes, so a stdout comparison over it compares two
 * empty strings and passes vacuously. That is the ambiguous zero this whole
 * reference-guest set exists to prevent.
 *
 * Deterministic by construction: no time, no pid, no randomness, no
 * environment, no filesystem. Same bytes on every host and every backend.
 */
#include <stdio.h>

int main(void) {
  unsigned long h = 1469598103934665603UL;
  for (int i = 0; i < 256; i++) {
    h ^= (unsigned long)i;
    h *= 1099511628211UL;
    printf("line %03d %016lx\n", i, h);
  }
  printf("stdout-lines=257 stdout-final=%016lx\n", h);
  return 0;
}
