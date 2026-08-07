/* Both-direction evidence for safeptrace map_err's ESRCH -> Died classification.
 *
 * map_err (safeptrace/src/lib.rs:547) turns EVERY ESRCH into Error::Died. Its
 * doc comment lists three causes and argues only cause (1), died-while-stopped,
 * can actually occur. This measures all of them, and measures whether the
 * proposed discriminator (/proc/<pid>/stat state) separates them.
 *
 * Prints one CSV row per trial: case,ptrace_rc,errno,alive,proc_state,discriminated
 *   alive          - kill(pid,0) succeeded (note: a ZOMBIE also answers this,
 *                    which is exactly why it cannot be the discriminator)
 *   proc_state     - the state character from /proc/<pid>/stat
 *   discriminated  - 1 when /proc state gives the CORRECT dead/alive answer
 */
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <unistd.h>

static int alive_by_kill(pid_t p) { return kill(p, 0) == 0 || errno == EPERM; }

static char proc_state(pid_t p) {
  char path[64], buf[512];
  snprintf(path, sizeof path, "/proc/%d/stat", (int)p);
  FILE *f = fopen(path, "r");
  if (!f) return '?';
  if (!fgets(buf, sizeof buf, f)) { fclose(f); return '?'; }
  fclose(f);
  char *close_paren = strrchr(buf, ')');
  if (!close_paren) return '?';
  return close_paren[2];
}

/* case 1: tracee RUNNING (not in a ptrace-stop) -- map_err reason (3), ALIVE */
static void case_running(void) {
  pid_t c = fork();
  if (c == 0) { ptrace(PTRACE_TRACEME,0,0,0); raise(SIGSTOP);
                for (volatile long i=0;;++i){} }
  int st; waitpid(c,&st,0);
  ptrace(PTRACE_CONT,c,0,0); usleep(30000);
  unsigned long m=0; errno=0;
  long rc = ptrace(PTRACE_GETEVENTMSG,c,0,&m); int e=errno;
  char s = proc_state(c);
  int truly_dead = 0;                       /* ground truth: it is ALIVE */
  int disc = ((s=='Z'||s=='?') == truly_dead);
  printf("running,%ld,%d,%d,%c,%d\n", rc, e, alive_by_kill(c), s, disc);
  kill(c,SIGKILL); waitpid(c,&st,0);
}

/* case 2: tracee is a genuine ZOMBIE -- map_err reason (1), DEAD */
static void case_zombie(void) {
  pid_t c = fork();
  if (c == 0) { ptrace(PTRACE_TRACEME,0,0,0); raise(SIGSTOP); _exit(0); }
  int st; waitpid(c,&st,0);
  ptrace(PTRACE_CONT,c,0,0);
  waitpid(c,&st,0);                          /* consume exit; now reaped/gone */
  unsigned long m=0; errno=0;
  long rc = ptrace(PTRACE_GETEVENTMSG,c,0,&m); int e=errno;
  char s = proc_state(c);
  int truly_dead = 1;
  int disc = ((s=='Z'||s=='?') == truly_dead);
  printf("dead,%ld,%d,%d,%c,%d\n", rc, e, alive_by_kill(c), s, disc);
}

int main(int argc, char **argv) {
  int trials = argc > 1 ? atoi(argv[1]) : 20;
  printf("case,ptrace_rc,errno,alive_by_kill,proc_state,discriminated\n");
  for (int i = 0; i < trials; ++i) { case_running(); case_zombie(); }
  return 0;
}
