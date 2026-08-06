/* Discriminate WHY the [stack] hash diverges after sysinfo(2).
   argv[1] selects the variant:
     null  - sysinfo(NULL): EFAULT, detcore writes nothing
     heap  - sysinfo into a HEAP buffer: stack untouched by the write
     stack - sysinfo into a 256-byte OVERSIZED stack buffer, guard-filled,
             dumped in full so an over-write past sizeof(struct sysinfo)=112
             becomes visible
     uname - uname(2) into a stack buffer: control, a different struct-writing
             syscall that the sweep already shows to be stable
*/
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <sys/sysinfo.h>
#include <sys/utsname.h>

static void dump(const char *tag, unsigned char *b, size_t n) {
  printf("%s:", tag);
  for (size_t i = 0; i < n; i++) printf("%02x", b[i]);
  printf("\n");
}

int main(int argc, char **argv) {
  const char *v = argc > 1 ? argv[1] : "stack";
  if (!strcmp(v, "null")) {
    int r = sysinfo(NULL);
    printf("null rc=%d errno=%d\n", r, r ? errno : 0);
  } else if (!strcmp(v, "heap")) {
    unsigned char *b = malloc(256); memset(b, 0xAA, 256);
    sysinfo((struct sysinfo *)b);
    dump("heap", b, 256);
  } else if (!strcmp(v, "uname")) {
    unsigned char b[512]; memset(b, 0xAA, sizeof b);
    uname((struct utsname *)b);
    printf("uname done\n");
  } else {
    unsigned char b[256]; memset(b, 0xAA, sizeof b);
    sysinfo((struct sysinfo *)b);
    dump("stack", b, 256);
  }
  return 0;
}
