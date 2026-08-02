#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

static void wait_ok(pid_t child) {
  int status = 0;
  pid_t r;
  do { r = waitpid(child, &status, 0); } while (r < 0 && errno == EINTR);
  if (r != child || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
    fprintf(stderr, "waitpid(%d) failed: r=%d status=%d\n", child, r, status);
    _exit(1);
  }
}

int main(void) {
  // depth-3 fork chain: parent -> child -> grandchild, plus a sibling fan.
  for (int i = 0; i < 3; ++i) {
    pid_t c = fork();
    if (c < 0) { perror("fork child"); return 1; }
    if (c == 0) {
      (void)syscall(SYS_getpid);
      pid_t g = fork();
      if (g < 0) { perror("fork grandchild"); _exit(1); }
      if (g == 0) {
        (void)syscall(SYS_getpid);
        (void)syscall(SYS_gettid);
        _exit(0);
      }
      wait_ok(g);
      _exit(0);
    }
    wait_ok(c);
  }
  puts("nested-fork-ok");
  return 0;
}
