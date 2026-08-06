/* vfork + exec: the known ptrace-stop hazard surface (detcore_misc livelock). */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 2;
  for (int i = 0; i < n; i++) {
    pid_t p = vfork();
    if (p == 0) { execl("/bin/true", "true", (char*)NULL); _exit(127); }
    int st = 0; waitpid(p, &st, 0);
    printf("vfork#%d status=%d\n", i, WEXITSTATUS(st)); fflush(stdout);
  }
  return 0;
}
