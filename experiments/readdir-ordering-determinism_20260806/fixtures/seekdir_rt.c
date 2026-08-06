// seekdir_rt: telldir/seekdir round-trip contract.
//
// POSIX: the value returned by telldir() before reading entry E, when passed to
// seekdir(), must resume the stream at E. This exercises the d_off cookie that
// Hermit permutes when it sorts a getdents64 result buffer.
//
//   usage: seekdir_rt <dir>
//
// Output: for each of the first N entries, the name read after seeking back to
// the position recorded before that entry, and a PASS/FAIL verdict.
#define _GNU_SOURCE
#include <dirent.h>
#include <stdio.h>
#include <string.h>

enum { MAXN = 64 };

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: %s <dir>\n", argv[0]);
    return 2;
  }
  DIR *d = opendir(argv[1]);
  if (!d) {
    perror("opendir");
    return 2;
  }

  long pos[MAXN];
  char names[MAXN][256];
  int n = 0;
  struct dirent *e;
  while (n < MAXN) {
    long p = telldir(d);
    e = readdir(d);
    if (!e) {
      break;
    }
    pos[n] = p;
    snprintf(names[n], sizeof names[n], "%s", e->d_name);
    n++;
  }

  int fails = 0;
  for (int i = 0; i < n; i++) {
    seekdir(d, pos[i]);
    struct dirent *g = readdir(d);
    const char *got = g ? g->d_name : "<NULL>";
    int ok = (strcmp(got, names[i]) == 0);
    if (!ok) {
      fails++;
    }
    printf("%d\texpect=%s\tgot=%s\t%s\n", i, names[i], got, ok ? "ok" : "MISMATCH");
  }
  closedir(d);
  printf("seekdir_roundtrip entries=%d mismatches=%d verdict=%s\n", n, fails,
         fails == 0 ? "PASS" : "FAIL");
  return 0;
}
