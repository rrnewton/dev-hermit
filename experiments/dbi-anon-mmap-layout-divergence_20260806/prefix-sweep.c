/*
 * Is ptrace's d23=-8 a property of the BACKEND, or of this guest's allocation
 * prefix?
 *
 * The prior three agents established that ptrace/native give d23=-8 and DBI
 * gives -4, and that DBI's address space contains no hole of the shape that
 * produces -8. That settles "can DBI be relocated to produce -8" (no). It does
 * NOT settle whether -8 is a stable reference value worth matching.
 *
 * The model in the task notes says -8 comes from ONE leftover free page in a
 * 7-page glibc loader gap: 1+2+3 pages fill 6 of 7, the 4-page request cannot
 * fit in the remaining 1, so it skips below the adjacent 3-page loader block
 * and lands 1+3+4 = 8 pages under ANON[2].
 *
 * If that model is right, consuming the gap FIRST removes the skip and ptrace
 * must produce the same contiguous packing DBI does. This guest does exactly
 * that: argv[1] pages of anonymous mmap before the 1+2+3+4 sequence.
 *
 * Prediction (no free parameters):
 *   prefix=0  ptrace d23=-8   (leftover 1 page, skip over 1+3)
 *   prefix=7  ptrace d23=-4   (gap consumed, all four pack contiguously)
 *   DBI       d23=-4 for every prefix (it packs contiguously already)
 */

#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>

enum { PAGE = 4096, NANON = 4 };

static long page_delta(const void *a, const void *b) {
    return (long)(((const char *)b - (const char *)a) / PAGE);
}

int main(int argc, char **argv) {
    long prefix_pages = (argc > 1) ? strtol(argv[1], NULL, 10) : 0;

    if (prefix_pages > 0) {
        void *p = mmap(NULL, (size_t)prefix_pages * PAGE, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (p == MAP_FAILED) { perror("mmap prefix"); return 2; }
    }

    void *a[NANON];
    for (int i = 0; i < NANON; i++) {
        a[i] = mmap(NULL, (size_t)(i + 1) * PAGE, PROT_READ | PROT_WRITE,
                    MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (a[i] == MAP_FAILED) { perror("mmap anon"); return 2; }
    }

    char cmp[16];
    int nc = 0;
    for (int i = 0; i < NANON; i++)
        for (int j = i + 1; j < NANON; j++)
            cmp[nc++] = (a[i] < a[j]) ? '1' : '0';
    cmp[nc] = '\0';

    printf("prefix=%ld cmp=%s d01=%ld d12=%ld d23=%ld\n",
           prefix_pages, cmp,
           page_delta(a[0], a[1]), page_delta(a[1], a[2]), page_delta(a[2], a[3]));
    return 0;
}
