/*
 * Stress family 2: fork + execve chains.
 *
 * The program re-executes itself in a chain: at depth D it prints a line, forks,
 * and the child execve()s the same binary with depth D-1 (plus a running
 * accumulator), while the parent wait4()s and folds the child's exit code into a
 * hash. This exercises repeated fork+execve (fresh address space each hop),
 * argv/argp handling, and wait4 status plumbing across a process chain.
 *
 * Additionally, at each depth it fans out WIDTH short-lived children that each
 * execve("/bin/true"-equivalent via self with depth 0) to add fork+exec volume.
 *
 * usage: forkexec_chain <depth> <acc>
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define WIDTH 3

extern char **environ;

static uint64_t fnv(uint64_t h, uint64_t v) {
    h ^= v;
    h *= 1099511628211ULL;
    return h;
}

int main(int argc, char **argv) {
    int depth = argc > 1 ? atoi(argv[1]) : 6;
    uint64_t acc = argc > 2 ? strtoull(argv[2], NULL, 10) : 1469598103934665603ULL;
    int is_top = argc < 3; /* the top frame is launched with only <depth>, no acc */

    /* self path for re-exec */
    char self[4096];
    ssize_t n = readlink("/proc/self/exe", self, sizeof self - 1);
    if (n <= 0) {
        /* fall back to argv[0] */
        strncpy(self, argv[0], sizeof self - 1);
        self[sizeof self - 1] = 0;
    } else {
        self[n] = 0;
    }

    acc = fnv(acc, (uint64_t)depth);

    /* leaf: just report and exit with a small deterministic code */
    if (depth <= 0) {
        printf("CHAIN_LEAF acc=%llu\n", (unsigned long long)acc);
        return (int)(acc & 0x3f);
    }

    printf("CHAIN_DEPTH %d acc=%llu pid_virt=%d\n", depth,
           (unsigned long long)acc, (int)getpid());

    /* fan-out: WIDTH short-lived fork+exec children at depth 0 */
    uint64_t fanhash = 1469598103934665603ULL;
    for (int w = 0; w < WIDTH; w++) {
        pid_t p = fork();
        if (p == 0) {
            char d0[] = "0";
            char accbuf[32];
            snprintf(accbuf, sizeof accbuf, "%llu",
                     (unsigned long long)(acc + w));
            char *av[] = {self, d0, accbuf, NULL};
            execve(self, av, environ);
            _exit(111); /* exec failed */
        }
        int st = 0;
        waitpid(p, &st, 0);
        if (WIFEXITED(st))
            fanhash = fnv(fanhash, (uint64_t)WEXITSTATUS(st));
    }
    printf("CHAIN_FANHASH depth=%d %llu\n", depth,
           (unsigned long long)fanhash);

    /* deep chain: fork + execve self at depth-1 */
    pid_t p = fork();
    if (p == 0) {
        char db[32], accbuf[32];
        snprintf(db, sizeof db, "%d", depth - 1);
        snprintf(accbuf, sizeof accbuf, "%llu",
                 (unsigned long long)fnv(acc, fanhash));
        char *av[] = {self, db, accbuf, NULL};
        execve(self, av, environ);
        _exit(111);
    }
    int st = 0;
    waitpid(p, &st, 0);
    int childcode = WIFEXITED(st) ? WEXITSTATUS(st) : -1;

    if (is_top) {
        /* only the true top frame prints the final summary */
        printf("CHAIN_TOP_CHILDCODE %d\n", childcode);
        printf("CHAIN_DONE\n");
        return 0; /* top exits 0 so --verify does not treat it as an error */
    }
    return childcode & 0x3f;
}
