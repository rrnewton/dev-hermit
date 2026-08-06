// dirls_libc: enumerate a directory with the ordinary glibc opendir/readdir
// path (the way real programs do it), printing entries in the order returned.
// glibc chooses the getdents64 buffer size itself, so this measures the
// realistic, user-visible enumeration order.
//
//   usage: dirls_libc <dir>
//
// Output (stdout), one line per entry: <seq>\t<name>
#define _GNU_SOURCE
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>

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
  struct dirent *e;
  long seq = 0;
  while ((e = readdir(d)) != NULL) {
    printf("%ld\t%s\n", seq++, e->d_name);
  }
  closedir(d);
  return 0;
}
