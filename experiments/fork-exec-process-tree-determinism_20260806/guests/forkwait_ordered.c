/* N children forked in order; parent waitpid()s each SPECIFIC pid in fork order.
   Tests: clone ordering, pid assignment order, targeted reap. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 4;
  pid_t kids[64];
  for (int i = 0; i < n; i++) {
    pid_t p = fork();
    if (p == 0) { printf("child %d\n", i); fflush(stdout); _exit(i + 1); }
    kids[i] = p;
    printf("forked slot=%d\n", i); fflush(stdout);
  }
  for (int i = 0; i < n; i++) {
    int st = 0; pid_t r = waitpid(kids[i], &st, 0);
    printf("reaped slot=%d ok=%d code=%d\n", i, r == kids[i], WEXITSTATUS(st)); fflush(stdout);
  }
  return 0;
}
