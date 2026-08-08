/* Dump every VMA the guest can see, so the two backends' address spaces can be
 * diffed as sets rather than eyeballed in a neighbourhood window. */
#include <stdio.h>
#include <string.h>
int main(void) {
  FILE *f = fopen("/proc/self/maps", "r");
  if (!f) { printf("NOMAPS\n"); return 1; }
  char line[512]; int n = 0;
  while (fgets(line, sizeof(line), f)) {
    unsigned long a, b; char perms[8]; char path[400] = "";
    if (sscanf(line, "%lx-%lx %7s %*s %*s %*s %399[^\n]", &a, &b, perms, path) >= 3) {
      const char *p = path; while (*p == ' ') p++;
      printf("VMA %6lup %-4s %s\n", (b - a) / 4096, perms, *p ? p : "[anon]");
      n++;
    }
  }
  fclose(f);
  printf("VMACOUNT %d\n", n);
  return 0;
}
