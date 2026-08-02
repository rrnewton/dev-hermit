#define _GNU_SOURCE

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(int argc, char **argv) {
  if (argc != 2) {
    fprintf(stderr, "usage: %s ITERATIONS\n", argv[0]);
    return 2;
  }
  errno = 0;
  char *end = NULL;
  unsigned long long iterations = strtoull(argv[1], &end, 10);
  if (errno != 0 || end == argv[1] || *end != '\0' || iterations == 0) {
    fprintf(stderr, "invalid iteration count: %s\n", argv[1]);
    return 2;
  }

  uint64_t checksum = 0;
  for (unsigned long long i = 0; i < iterations; ++i) {
    checksum += (uint64_t)syscall(SYS_getpid);
  }
  printf("iterations=%llu checksum=%llu\n", iterations,
         (unsigned long long)checksum);
  return 0;
}
