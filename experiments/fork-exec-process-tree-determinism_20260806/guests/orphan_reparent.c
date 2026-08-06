/* double fork: the middle process exits first, orphaning the grandchild,
   which is reparented. Tests zombie/reparent bookkeeping ordering. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
int main(void) {
  pid_t p = fork();
  if (p == 0) {
    pid_t g = fork();
    if (g == 0) { volatile unsigned long a=0; for (unsigned long k=0;k<400000;k++) a+=k;
                  printf("grandchild done\n"); fflush(stdout); _exit(7); }
    printf("middle exiting, grandchild orphaned\n"); fflush(stdout);
    _exit(3);
  }
  int st = 0; waitpid(p, &st, 0);
  printf("parent reaped middle code=%d\n", WEXITSTATUS(st)); fflush(stdout);
  /* give the orphan time to finish under the deterministic scheduler */
  for (volatile unsigned long k = 0; k < 800000; k++) ;
  printf("parent done\n"); fflush(stdout);
  return 0;
}
