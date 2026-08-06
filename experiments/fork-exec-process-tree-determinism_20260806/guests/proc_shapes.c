/* Process-event shapes the first corpus never emitted: clone3, setsid/setpgid
   (session + process-group ordering), and waitid with WNOWAIT (a peek that must
   NOT consume the zombie, then a real reap). */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sched.h>
#include <sys/wait.h>
#include <sys/syscall.h>
#include <linux/sched.h>

struct clone_args3 { unsigned long long flags, pidfd, child_tid, parent_tid, exit_signal,
                     stack, stack_size, tls; };

int main(void) {
  /* --- clone3 --- */
  for (int i = 0; i < 2; i++) {
    struct clone_args3 ca; memset(&ca, 0, sizeof ca);
    ca.exit_signal = SIGCHLD;
    long p = syscall(SYS_clone3, &ca, sizeof ca);
    if (p == 0) { printf("clone3 child %d\n", i); fflush(stdout); _exit(20 + i); }
    if (p < 0) { printf("clone3 unsupported\n"); fflush(stdout); break; }
    int st = 0; pid_t r = waitpid((pid_t)p, &st, 0);
    printf("clone3 reap ok=%d code=%d\n", r == (pid_t)p, WEXITSTATUS(st)); fflush(stdout);
  }
  /* --- setsid / setpgid in a child --- */
  pid_t s = fork();
  if (s == 0) {
    pid_t sid = setsid();
    printf("setsid ok=%d self-leader=%d\n", sid > 0, sid == getpid()); fflush(stdout);
    _exit(5);
  }
  { int st = 0; waitpid(s, &st, 0); printf("setsid child code=%d\n", WEXITSTATUS(st)); fflush(stdout); }

  pid_t g = fork();
  if (g == 0) { if (setpgid(0, 0)) _exit(1); printf("setpgid own-group\n"); fflush(stdout); _exit(6); }
  { int st = 0; waitpid(g, &st, 0); printf("setpgid child code=%d\n", WEXITSTATUS(st)); fflush(stdout); }

  /* --- waitid WNOWAIT: peek must not consume the zombie --- */
  pid_t z = fork();
  if (z == 0) _exit(9);
  siginfo_t si; memset(&si, 0, sizeof si);
  int a = waitid(P_PID, z, &si, WEXITED | WNOWAIT);
  printf("waitid peek rc=%d pid-match=%d code=%d\n", a, si.si_pid == z, si.si_status); fflush(stdout);
  memset(&si, 0, sizeof si);
  int b = waitid(P_PID, z, &si, WEXITED);
  printf("waitid reap rc=%d pid-match=%d code=%d\n", b, si.si_pid == z, si.si_status); fflush(stdout);
  return 0;
}
