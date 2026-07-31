#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <sys/wait.h>
int main(void){
  pid_t p = fork();
  if(p==0){ pause(); _exit(0); }        /* child lingers */
  errno=0;
  long fd = syscall(SYS_pidfd_open, p, 0);    /* 434 */
  printf("pidfd_open(%d) = %ld errno=%d (%s)\n", p, fd, errno, strerror(errno));
  errno=0;
  long r = syscall(SYS_pidfd_send_signal, fd, 9, 0, 0); /* 424 */
  printf("pidfd_send_signal = %ld errno=%d (%s)\n", r, errno, strerror(errno));
  errno=0;
  long k = syscall(SYS_kill, p, 9);
  printf("kill(%d,9) = %ld errno=%d (%s)\n", p, k, errno, strerror(errno));
  int st; waitpid(p,&st,0);
  return 0;
}
