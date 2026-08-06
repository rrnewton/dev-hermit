/* posix_spawn (glibc uses CLONE_VM|CLONE_VFORK internally) + waitpid.
   Different creation path than fork(), same reap surface. */
#include <stdio.h>
#include <stdlib.h>
#include <spawn.h>
#include <sys/wait.h>
extern char **environ;
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 3;
  pid_t kids[32];
  char *av[] = {"/bin/echo", "spawned", NULL};
  for (int i = 0; i < n; i++)
    if (posix_spawn(&kids[i], "/bin/echo", NULL, NULL, av, environ) != 0) return 1;
  for (int i = 0; i < n; i++) {
    int st = 0; pid_t r = waitpid(kids[i], &st, 0);
    printf("spawn reap#%d ok=%d code=%d\n", i, r == kids[i], WEXITSTATUS(st)); fflush(stdout);
  }
  return 0;
}
