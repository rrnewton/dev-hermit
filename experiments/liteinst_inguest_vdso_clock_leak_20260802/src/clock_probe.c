#define _GNU_SOURCE
#include <stdio.h>
#include <time.h>
#include <sys/syscall.h>
#include <unistd.h>
int main(void){
  struct timespec vd={0}, rw={0};
  clock_gettime(CLOCK_REALTIME,&vd);              /* libc -> vDSO fast path */
  syscall(SYS_clock_gettime,CLOCK_REALTIME,&rw);  /* raw syscall, bypasses vDSO */
  printf("vdso=%ld raw=%ld\n",(long)vd.tv_sec,(long)rw.tv_sec);
  return 0;
}
