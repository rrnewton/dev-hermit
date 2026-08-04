// Non-leader thread execve continuity + identity probe.
//
// A secondary (non-leader) thread calls execve. In Linux this destroys all
// other threads and the caller becomes the new thread-group leader, taking over
// the tgid (PID). gettid() therefore == getpid() after exec, even though the
// EXECING thread was a non-leader before.
//
// We measure CLOCK_MONOTONIC (hermit virtualizes this) at PRE (in the worker,
// just before execve) and POST (in the re-exec'd image). Continuous virtual
// time must be MONOTONIC across the exec boundary: t_post >= t_pre. A reset to
// the epoch baseline (t_post << t_pre) is a continuous-virtual-time violation.
#define _GNU_SOURCE
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/syscall.h>

static long gettid_(void) { return syscall(SYS_gettid); }

static unsigned long long mono_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned long long)ts.tv_sec * 1000000000ULL + (unsigned long long)ts.tv_nsec;
}

static const char *g_self;

static void *worker(void *arg) {
    (void)arg;
    // Advance virtual time a bit with observable work (syscalls advance the
    // logical clock deterministically under hermit).
    volatile unsigned long acc = 0;
    for (int i = 0; i < 2000; i++) { acc += (unsigned long)getpid(); }
    unsigned long long t_pre = mono_ns();
    // Non-leader identity: gettid != getpid here.
    printf("PRE  tid=%ld pid=%d leader=%d t_pre=%llu\n",
           gettid_(), getpid(), (gettid_() == getpid()), t_pre);
    fflush(stdout);
    char *const argv[] = { (char *)g_self, (char *)"--post", NULL };
    char *const envp[] = { NULL };
    execve(g_self, argv, envp);
    perror("execve");
    _exit(97);
    return NULL;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc >= 2 && strcmp(argv[1], "--post") == 0) {
        unsigned long long t_post = mono_ns();
        // After a non-leader exec, the surviving thread IS the leader: tid==pid.
        printf("POST tid=%ld pid=%d leader=%d t_post=%llu\n",
               gettid_(), getpid(), (gettid_() == getpid()), t_post);
        fflush(stdout);
        return 0;
    }
    g_self = argv[0];
    pthread_t th;
    if (pthread_create(&th, NULL, worker, NULL) != 0) { perror("pthread_create"); return 1; }
    // Leader waits; the worker will execve, destroying us. If we somehow return,
    // print so the divergence is visible.
    pthread_join(th, NULL);
    printf("LEADER-RETURNED (unexpected: exec should have replaced image)\n");
    return 0;
}
