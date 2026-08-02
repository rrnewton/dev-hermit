#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

int main(void) {
  pid_t c = fork();
  if (c < 0) { fprintf(stderr, "L1 fork errno=%d\n", errno); return 1; }
  if (c == 0) {
    // child1: try to fork a grandchild
    pid_t g = fork();
    if (g < 0) { fprintf(stderr, "L2 fork errno=%d (raw)\n", errno); _exit(3); }
    if (g == 0) { _exit(0); }
    int st=0; waitpid(g,&st,0);
    _exit(WIFEXITED(st)&&WEXITSTATUS(st)==0 ? 0 : 4);
  }
  int st=0; waitpid(c,&st,0);
  if (!WIFEXITED(st)) { fprintf(stderr,"child1 not exited\n"); return 5; }
  fprintf(stderr, "child1 exit=%d\n", WEXITSTATUS(st));
  if (WEXITSTATUS(st)==0) puts("nested-min-ok");
  return WEXITSTATUS(st);
}
