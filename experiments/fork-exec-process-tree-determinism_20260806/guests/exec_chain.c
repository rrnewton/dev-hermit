/* fork + execve a chain: each generation execs the same binary with depth-1. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
  int d = argc > 1 ? atoi(argv[1]) : 3;
  printf("depth %d pid-order-ok\n", d); fflush(stdout);
  if (d <= 0) return 0;
  char buf[16]; snprintf(buf, sizeof buf, "%d", d - 1);
  pid_t p = fork();
  if (p == 0) { execl(argv[0], argv[0], buf, (char*)NULL); perror("execl"); _exit(127); }
  int st = 0; waitpid(p, &st, 0);
  printf("depth %d child exited %d\n", d, WEXITSTATUS(st)); fflush(stdout);
  return 0;
}
