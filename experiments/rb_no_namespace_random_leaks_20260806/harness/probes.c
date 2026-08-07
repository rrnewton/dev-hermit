/* Probes the candidate nondeterminism sources named by
 * experiments/nix-hermit-execbuilder-prototype_20260729. One line per source so
 * a caller can diff sources independently. */
#include <stdio.h>
#include <sys/auxv.h>
#include <sys/time.h>
#include <unistd.h>

int main(void) {
  unsigned char *r = (unsigned char *)getauxval(AT_RANDOM);
  printf("at_random=");
  if (!r) printf("NULL");
  else for (int i = 0; i < 16; i++) printf("%02x", r[i]);
  printf("\n");

  struct timeval tv;
  gettimeofday(&tv, NULL);
  printf("gettimeofday=%ld.%06ld\n", (long)tv.tv_sec, (long)tv.tv_usec);
  printf("getpid=%d\n", (int)getpid());
  return 0;
}
