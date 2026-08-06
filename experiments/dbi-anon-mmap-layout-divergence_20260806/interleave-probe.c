/* Does anything of DynamoRIO's sit BETWEEN the guest's four anonymous mmaps?
 * That is the dispatch's hypothesis ("translator allocations intermixing with
 * the guest's anon mmaps, potentially excludable from the count"). It is a
 * question about VMAs in a span, so measure the span. */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/mman.h>
enum { PAGE = 4096, NANON = 4 };
int main(void) {
    void *a[NANON];
    for (int i = 0; i < NANON; i++) {
        a[i] = mmap(NULL, (size_t)(i+1)*PAGE, PROT_READ|PROT_WRITE,
                    MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
        if (a[i] == MAP_FAILED) { perror("mmap"); return 2; }
    }
    unsigned long lo = (unsigned long)a[NANON-1];              /* lowest  */
    unsigned long hi = (unsigned long)a[0] + 1*PAGE;           /* highest end */
    for (int i = 0; i < NANON; i++) {
        unsigned long s = (unsigned long)a[i], e = s + (unsigned long)(i+1)*PAGE;
        if (s < lo) lo = s;
        if (e > hi) hi = e;
    }
    printf("span %lx-%lx = %lu pages; guest asked for %d pages total\n",
           lo, hi, (hi-lo)/PAGE, 1+2+3+4);
    /* every VMA overlapping the span */
    FILE *f = fopen("/proc/self/maps", "r");
    char line[512];
    int n = 0;
    while (fgets(line, sizeof line, f)) {
        unsigned long s, e;
        if (sscanf(line, "%lx-%lx", &s, &e) != 2) continue;
        if (e <= lo || s >= hi) continue;
        line[strcspn(line, "\n")] = 0;
        printf("  VMA[%d] %s\n", n++, line);
    }
    fclose(f);
    printf("VMAS_OVERLAPPING_SPAN=%d\n", n);
    return 0;
}
