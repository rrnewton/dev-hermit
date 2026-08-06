// sysinfo_padding: does sysinfo(2) leave any byte of the guest's struct
// uninitialized, and does that byte vary between runs?
//
//   usage: sysinfo_padding [--hex] [--repeat N]
//
// Method: poison the whole struct with 0xAA, call sysinfo(2), then report
//   - the full 112-byte image as hex (with --hex),
//   - a FNV-1a hash of the full struct image,
//   - a hash of the ABI-defined FIELDS only (padding excluded),
//   - the exact bytes at the two x86_64 padding windows.
//
// If the struct hash varies run to run while the field hash does not, the
// difference is entirely padding: a syscall that reports success while
// injecting nondeterminism.
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/sysinfo.h>

// x86_64 struct sysinfo: 112 bytes.
//   0   long uptime
//   8   unsigned long loads[3]
//   32  totalram  40 freeram  48 sharedram  56 bufferram
//   64  totalswap 72 freeswap
//   80  unsigned short procs
//   82  unsigned short pad          <- ABI field, Linux always writes 0
//   84  [4 bytes implicit padding]
//   88  totalhigh 96 freehigh
//   104 unsigned int mem_unit
//   108 [4 bytes implicit padding / char _f[0]]
enum { SI_SIZE = 112, PAD_A = 82, PAD_A_LEN = 6, PAD_B = 108, PAD_B_LEN = 4 };

static unsigned long long fnv(const unsigned char *p, size_t n) {
  unsigned long long h = 1469598103934665603ULL;
  for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211ULL; }
  return h;
}

// Hash only the ABI-defined field bytes: [0,82) and [84,108).
// (82..84 is the `pad` field, which Linux defines and zeroes, but which a
// determinizer may leave arbitrary; it is counted as padding here and
// reported separately.)
static unsigned long long fnv_fields(const unsigned char *p) {
  unsigned long long h = 1469598103934665603ULL;
  for (size_t i = 0; i < 82; i++)  { h ^= p[i]; h *= 1099511628211ULL; }
  for (size_t i = 88; i < 108; i++){ h ^= p[i]; h *= 1099511628211ULL; }
  return h;
}

static void hexdump(const char *label, const unsigned char *p, size_t off, size_t n) {
  printf("%s[%zu..%zu) =", label, off, off + n);
  for (size_t i = 0; i < n; i++) printf(" %02x", p[off + i]);
  printf("\n");
}

int main(int argc, char **argv) {
  int hex = 0, repeat = 1;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--hex")) hex = 1;
    else if (!strcmp(argv[i], "--repeat") && i + 1 < argc) repeat = atoi(argv[++i]);
  }

  if (sizeof(struct sysinfo) != SI_SIZE) {
    printf("UNEXPECTED sizeof(struct sysinfo)=%zu (expected %d)\n",
           sizeof(struct sysinfo), SI_SIZE);
    return 2;
  }

  for (int r = 0; r < repeat; r++) {
    unsigned char buf[SI_SIZE];
    memset(buf, 0xAA, sizeof buf);          // poison
    struct sysinfo *si = (struct sysinfo *)buf;
    if (sysinfo(si) != 0) { perror("sysinfo"); return 2; }

    printf("call %d struct_hash=%016llx field_hash=%016llx\n", r,
           fnv(buf, SI_SIZE), fnv_fields(buf));
    hexdump("  padA", buf, PAD_A, PAD_A_LEN);
    hexdump("  padB", buf, PAD_B, PAD_B_LEN);
    if (hex) {
      printf("  full =");
      for (int i = 0; i < SI_SIZE; i++) printf(" %02x", buf[i]);
      printf("\n");
    }
  }
  return 0;
}
