/* Print RAW guest-visible address-space facts. No normalization: any run-to-run
 * difference in this output is guest-observable nondeterminism by definition. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
static int g_static;
int main(void) {
    void *m1 = mmap(NULL, 4096, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    void *m2 = mmap(NULL, 65536, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    void *m3 = mmap(NULL, 4096, PROT_READ, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    void *brk0 = sbrk(0);
    void *heap = malloc(1024);
    int local;
    printf("mmap1     %p\n", m1);
    printf("mmap2     %p\n", m2);
    printf("mmap3     %p\n", m3);
    printf("mmap2-1   %ld\n", (long)((char*)m2 - (char*)m1));
    printf("brk0      %p\n", brk0);
    printf("malloc    %p\n", heap);
    printf("stacklocal %p\n", (void*)&local);
    printf("static    %p\n", (void*)&g_static);
    printf("main      %p\n", (void*)(size_t)main);
    /* aliasing/ordering signal that survives even if bases shift */
    printf("ord m1<m2 %d  m2<m3 %d\n", m1 < m2, m2 < m3);
    return 0;
}
