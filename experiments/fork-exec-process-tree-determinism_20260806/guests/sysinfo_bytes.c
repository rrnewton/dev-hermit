/* Dump the RAW BYTES of the struct sysinfo that the guest receives, so the
   divergence can be attributed to specific byte offsets rather than inferred
   from the stack hash. Pre-fills the buffer with a known pattern so any byte
   detcore does NOT write is visible as 0xAA. */
#include <stdio.h>
#include <string.h>
#include <sys/sysinfo.h>
int main(void) {
  unsigned char buf[sizeof(struct sysinfo)];
  memset(buf, 0xAA, sizeof buf);
  if (sysinfo((struct sysinfo *)buf)) return 1;
  printf("sizeof=%zu\n", sizeof buf);
  for (size_t i = 0; i < sizeof buf; i++) {
    printf("%02x", buf[i]);
    if ((i % 8) == 7) printf(" off=%zu\n", i - 7);
  }
  printf("\n");
  return 0;
}
