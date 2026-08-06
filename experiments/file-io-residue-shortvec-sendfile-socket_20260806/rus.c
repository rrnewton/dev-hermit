/* Stack 2.1 evidence guest: sample getrusage/times BEFORE and AFTER real CPU work.
 * The point is not just "deterministic" -- zeroes are trivially deterministic. It is that the
 * value must ADVANCE with work (continuity, #140) and be IDENTICAL across runs. */
#define _GNU_SOURCE
#include <stdio.h>
#include <sys/resource.h>
#include <sys/times.h>
int main(void) {
    struct rusage a, b; struct tms ta, tb;
    getrusage(RUSAGE_SELF, &a); times(&ta);
    volatile double x = 0;
    for (long i = 1; i < 30000000L; i++) x += 1.0 / (double)i;
    getrusage(RUSAGE_SELF, &b); times(&tb);
    printf("utime %ld.%06ld -> %ld.%06ld\n", (long)a.ru_utime.tv_sec, (long)a.ru_utime.tv_usec,
           (long)b.ru_utime.tv_sec, (long)b.ru_utime.tv_usec);
    printf("stime %ld.%06ld -> %ld.%06ld\n", (long)a.ru_stime.tv_sec, (long)a.ru_stime.tv_usec,
           (long)b.ru_stime.tv_sec, (long)b.ru_stime.tv_usec);
    printf("maxrss %ld -> %ld\n", a.ru_maxrss, b.ru_maxrss);
    printf("tms_utime %ld -> %ld  sink=%d\n", (long)ta.tms_utime, (long)tb.tms_utime, x > 0);
    return 0;
}
