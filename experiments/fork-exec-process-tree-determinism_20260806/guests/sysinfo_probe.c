/* SINGLE-PROCESS bracket for the sh_pipeline [stack]-hash divergence.
   No fork, no exec, no wait: if this diverges under --detlog-stack then the
   divergence is a sysinfo determinization gap, NOT a process-tree defect. */
#include <stdio.h>
#include <string.h>
#include <sys/sysinfo.h>
int main(int argc, char **argv) {
  struct sysinfo si;
  memset(&si, 0, sizeof si);
  if (argc > 1) {              /* positive control: same guest, no sysinfo */
    printf("skipped sysinfo\n");
    return 0;
  }
  if (sysinfo(&si)) return 1;
  /* Deliberately do NOT print the fields: the leak must be caught by the
     stack hash, not by stdout, exactly as in the pipeline cell. */
  printf("sysinfo ok\n");
  return 0;
}
