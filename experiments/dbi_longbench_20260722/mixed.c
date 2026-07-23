/* Mixed compute + syscall: compute chunk then a syscall, repeated. */
#include <stdio.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <fcntl.h>
int main(void){
  int fd=open("/dev/null",O_WRONLY);
  volatile unsigned long s=0;
  for(int r=0;r<90000;r++){
    for(unsigned long i=0;i<100000UL;i++) s+=i;   /* compute chunk */
    syscall(SYS_getpid);                          /* syscall */
    if((r&15)==0) write(fd,"x",1);                /* periodic write */
  }
  printf("%lu\n", s); return 0; }
