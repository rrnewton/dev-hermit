// dirls_raw: enumerate a directory with RAW getdents64(2) at a caller-chosen
// buffer size, printing one line per entry in exactly the order the kernel (or
// Hermit's determinizer) hands them back.
//
//   usage: dirls_raw <dir> <bufsize> [--batches] [--offs]
//
// Output (stdout), one line per entry:
//   <seq>\t<name>
// With --batches, a line "-- batch <k> nbytes=<n> nents=<m>" precedes each
// getdents64 result, so batch boundaries are visible.
// With --offs, the line becomes "<seq>\t<name>\t<d_off>\t<d_ino>" and a final
// line reports whether d_off was monotonically increasing over the whole
// stream -- Linux guarantees it is; a determinizer that reorders records
// within a buffer without reissuing cookies breaks that guarantee.
//
// Rationale: opendir/readdir hides the buffer size (glibc picks it), and the
// batch boundary is exactly the thing under test -- Hermit sorts each
// getdents64 *result buffer* independently, so where the boundaries fall
// determines the global enumeration order.
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

struct linux_dirent64 {
  unsigned long long d_ino;
  long long d_off;
  unsigned short d_reclen;
  unsigned char d_type;
  char d_name[];
};

int main(int argc, char **argv) {
  if (argc < 3) {
    fprintf(stderr, "usage: %s <dir> <bufsize> [--batches]\n", argv[0]);
    return 2;
  }
  const char *dir = argv[1];
  long bufsz = strtol(argv[2], NULL, 10);
  int show_batches = 0, show_offs = 0;
  for (int i = 3; i < argc; i++) {
    if (strcmp(argv[i], "--batches") == 0) {
      show_batches = 1;
    } else if (strcmp(argv[i], "--offs") == 0) {
      show_offs = 1;
    }
  }
  if (bufsz < 64 || bufsz > (1 << 22)) {
    fprintf(stderr, "bufsize out of range\n");
    return 2;
  }

  char *buf = malloc((size_t)bufsz);
  if (!buf) {
    fprintf(stderr, "malloc failed\n");
    return 2;
  }

  int fd = open(dir, O_RDONLY | O_DIRECTORY);
  if (fd < 0) {
    perror("open");
    return 2;
  }

  long seq = 0;
  long batch = 0;
  long long prev_off = -1;
  long off_inversions = 0;
  for (;;) {
    long nread = syscall(SYS_getdents64, fd, buf, bufsz);
    if (nread < 0) {
      perror("getdents64");
      return 2;
    }
    if (nread == 0) {
      break;
    }
    long nents = 0;
    for (long bpos = 0; bpos < nread;) {
      struct linux_dirent64 *d = (struct linux_dirent64 *)(buf + bpos);
      nents++;
      bpos += d->d_reclen;
    }
    if (show_batches) {
      printf("-- batch %ld nbytes=%ld nents=%ld\n", batch, nread, nents);
    }
    for (long bpos = 0; bpos < nread;) {
      struct linux_dirent64 *d = (struct linux_dirent64 *)(buf + bpos);
      if (show_offs) {
        printf("%ld\t%s\t%lld\t%llu\n", seq++, d->d_name, d->d_off, d->d_ino);
        if (prev_off >= 0 && d->d_off <= prev_off) {
          off_inversions++;
        }
        prev_off = d->d_off;
      } else {
        printf("%ld\t%s\n", seq++, d->d_name);
      }
      bpos += d->d_reclen;
    }
    batch++;
  }
  if (show_offs) {
    printf("d_off_monotonic entries=%ld inversions=%ld verdict=%s\n", seq,
           off_inversions, off_inversions == 0 ? "PASS" : "FAIL");
  }
  close(fd);
  free(buf);
  return 0;
}
