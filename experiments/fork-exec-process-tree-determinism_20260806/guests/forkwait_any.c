/* N children; parent reaps with wait(-1) => REAP ORDER is scheduler-determined.
   This is the sharpest process-tree ordering probe: the sequence of reaped
   slots must be identical run-to-run and backend-to-backend. Children do
   different amounts of work so a nondeterministic scheduler would reorder. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 5;
  pid_t kids[64];
  for (int i = 0; i < n; i++) {
    pid_t p = fork();
    if (p == 0) {
      volatile unsigned long acc = 0;
      /* reverse-ordered work: later children finish FIRST on a real machine */
      for (unsigned long k = 0; k < (unsigned long)(n - i) * 200000UL; k++) acc += k;
      _exit((int)(acc & 0x1f) == -1 ? 1 : i + 1);
    }
    kids[i] = p;
  }
  for (int i = 0; i < n; i++) {
    int st = 0; pid_t r = wait(&st);
    int slot = -1;
    for (int j = 0; j < n; j++) if (kids[j] == r) slot = j;
    printf("reap#%d slot=%d code=%d\n", i, slot, WEXITSTATUS(st)); fflush(stdout);
  }
  return 0;
}
