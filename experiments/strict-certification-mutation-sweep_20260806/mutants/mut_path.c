
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
int main(void){
  long n = bump();
  char path[256]; snprintf(path, sizeof path, "/tmp/w7mutpath_%ld", n);
  int fd = open(path, O_RDWR|O_CREAT, 0644); if (fd>=0) close(fd);
  printf("constant-output\n"); return 0;
}
