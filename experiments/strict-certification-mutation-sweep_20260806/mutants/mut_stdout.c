
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
static long bump(void){
  int fd = open("/tmp/w7mutstate", O_RDWR|O_CREAT|O_APPEND, 0644);
  if (fd < 0) { perror("open state"); exit(99); }
  if (write(fd, "x", 1) != 1) { perror("write state"); exit(98); }
  long n = lseek(fd, 0, SEEK_END);
  close(fd);
  return n;
}
int main(void){ long n = bump(); printf("counter=%ld\n", n); return 0; }
