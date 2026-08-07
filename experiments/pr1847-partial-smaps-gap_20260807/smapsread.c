/* Same shape as notsc.c, but reads /proc/self/smaps into a live stack buffer.
   If the smaps inode column is still host-global, the stack hash must diverge. */
#include <stdio.h>
#include <unistd.h>
int main(void) {
    volatile char buf[4096];
    FILE *f = fopen("/proc/self/smaps", "r");
    if (!f) { printf("smaps_missing\n"); return 1; }
    for (int i = 0; i < 4096; ++i) buf[i] = 0;
    size_t n = fread((void *)buf, 1, 4095, f);
    fclose(f);
    for (int i = 0; i < 8; ++i) { (void)getpid(); }
    int acc = 0;
    for (int i = 0; i < 4096; ++i) acc += buf[i];
    printf("smaps_read=%d\n", (n > 0) && (acc != 0));
    return 0;
}
