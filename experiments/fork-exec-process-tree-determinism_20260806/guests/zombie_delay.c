/* children exit immediately and sit as zombies while the parent burns CPU,
   then are reaped en masse. Tests zombie retention + bulk reap ordering. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 4;
  for (int i = 0; i < n; i++) { pid_t p = fork(); if (p == 0) _exit(i + 1); }
  volatile unsigned long a = 0; for (unsigned long k = 0; k < 1000000; k++) a += k;
  for (int i = 0; i < n; i++) {
    int st = 0; pid_t r = wait(&st);
    printf("bulk reap#%d code=%d\n", i, WEXITSTATUS(st)); fflush(stdout);
  }
  return 0;
}
