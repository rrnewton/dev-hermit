#define _GNU_SOURCE
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

int main(int argc, char **argv) {
  if (argc != 3) {
    fprintf(stderr, "usage: %s <vdso|raw> <iterations>\n", argv[0]);
    return 2;
  }

  char *end = NULL;
  errno = 0;
  unsigned long iterations = strtoul(argv[2], &end, 10);
  if (errno != 0 || end == argv[2] || *end != '\0') {
    fprintf(stderr, "invalid iterations: %s\n", argv[2]);
    return 2;
  }

  const int raw = strcmp(argv[1], "raw") == 0;
  if (!raw && strcmp(argv[1], "vdso") != 0) {
    fprintf(stderr, "invalid mode: %s\n", argv[1]);
    return 2;
  }

  uint64_t checksum = 0;
  for (unsigned long i = 0; i < iterations; ++i) {
    struct timespec ts;
    int rc = raw ? (int)syscall(SYS_clock_gettime, CLOCK_REALTIME, &ts)
                 : clock_gettime(CLOCK_REALTIME, &ts);
    if (rc != 0) {
      perror("clock_gettime");
      return 1;
    }
    checksum += (uint64_t)ts.tv_nsec;
  }

  printf("mode=%s iterations=%lu checksum=%llu\n", argv[1], iterations,
         (unsigned long long)checksum);
  return 0;
}
