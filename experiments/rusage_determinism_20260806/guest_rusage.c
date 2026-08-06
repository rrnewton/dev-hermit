/* Raw resource-accounting facts, sampled BEFORE and AFTER real work, so the
 * output shows both determinism (run-to-run) and EVOLUTION (within a run). */
#define _GNU_SOURCE
#include <stdio.h>
#include <sys/resource.h>
#include <sys/times.h>
#include <unistd.h>
static void dump(const char *when) {
    struct rusage r; getrusage(RUSAGE_SELF, &r);
    struct tms t; clock_t ct = times(&t);
    printf("%s rusage utime=%ld.%06ld stime=%ld.%06ld maxrss=%ld minflt=%ld majflt=%ld nvcsw=%ld nivcsw=%ld\n",
        when, (long)r.ru_utime.tv_sec, (long)r.ru_utime.tv_usec,
        (long)r.ru_stime.tv_sec, (long)r.ru_stime.tv_usec,
        r.ru_maxrss, r.ru_minflt, r.ru_majflt, r.ru_nvcsw, r.ru_nivcsw);
    printf("%s times  ret=%ld utime=%ld stime=%ld\n", when, (long)ct, (long)t.tms_utime, (long)t.tms_stime);
}
int main(void) {
    struct rlimit rl;
    getrlimit(RLIMIT_NOFILE, &rl);
    printf("rlimit NOFILE soft=%ld hard=%ld\n", (long)rl.rlim_cur, (long)rl.rlim_max);
    getrlimit(RLIMIT_STACK, &rl);
    printf("rlimit STACK  soft=%ld hard=%ld\n", (long)rl.rlim_cur, (long)rl.rlim_max);
    dump("BEFORE");
    volatile double x = 0; for (long i = 0; i < 30000000L; i++) x += i * 0.5;  /* real CPU work */
    dump("AFTER ");
    printf("sink %f\n", (double)x);
    return 0;
}
