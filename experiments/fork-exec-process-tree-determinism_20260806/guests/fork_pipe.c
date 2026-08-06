/* fork + pipe rendezvous: children write, parent reads until EOF, then reaps.
   The pipe couples process ORDER to data ORDER, so a scheduling divergence
   shows up in the guest's own stdout, not only in the detlog. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 4;
  int fd[2]; if (pipe(fd)) return 1;
  for (int i = 0; i < n; i++) {
    pid_t p = fork();
    if (p == 0) {
      close(fd[0]);
      volatile unsigned long a = 0;
      for (unsigned long k = 0; k < (unsigned long)(n - i) * 150000UL; k++) a += k;
      char buf[32]; int m = snprintf(buf, sizeof buf, "from %d\n", i);
      ssize_t w = write(fd[1], buf, m); (void)w;
      _exit(0);
    }
  }
  close(fd[1]);
  char buf[512]; ssize_t r, tot = 0;
  while ((r = read(fd[0], buf + tot, sizeof buf - tot - 1)) > 0) tot += r;
  buf[tot] = 0;
  fputs(buf, stdout); fflush(stdout);
  for (int i = 0; i < n; i++) { int st; wait(&st); }
  printf("all reaped\n"); fflush(stdout);
  return 0;
}
