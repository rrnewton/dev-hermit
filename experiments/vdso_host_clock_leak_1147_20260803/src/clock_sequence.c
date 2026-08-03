#define _GNU_SOURCE
#include <stdio.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

int main(void) {
  for (int i = 0; i < 8; ++i) {
    struct timespec vdso = {0};
    struct timespec raw = {0};
    if (clock_gettime(CLOCK_REALTIME, &vdso) != 0 ||
        syscall(SYS_clock_gettime, CLOCK_REALTIME, &raw) != 0) {
      perror("clock_gettime");
      return 1;
    }
    printf("%d vdso=%ld.%09ld raw=%ld.%09ld\n", i, (long)vdso.tv_sec,
           vdso.tv_nsec, (long)raw.tv_sec, raw.tv_nsec);
  }
  return 0;
}
