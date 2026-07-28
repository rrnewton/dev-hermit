/*
 * fork+pipe stress test for Hermit ptrace-compat determinism.
 *
 * Exercises the historically-hard cases for --strict --verify:
 *   - high fork volume (NCHILD children)
 *   - concurrent pipe writers fanning in to one reader
 *   - scheduling-sensitive OUTPUT (the byte-arrival order across children is
 *     recorded and digested, so any nondeterministic interleaving shows up as a
 *     verify divergence, not just a silently-different checksum)
 *
 * Every child writes MSGS fixed-size records into its own pipe; the parent
 * poll()s all read ends and drains them, appending each arriving child id to an
 * order log. At the end it prints:
 *   - CHECKSUM: order-independent (must always be the same value)
 *   - ORDERDIG:  order-DEPENDENT rolling hash of the arrival sequence
 *   - ORDERHEAD: first 64 arrivals, literally
 * Under a correct deterministic scheduler all three are bitwise-identical on
 * every run; a scheduler race would perturb ORDERDIG/ORDERHEAD.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define NCHILD 16
#define MSGS 64
#define REC 4 /* bytes per record */

int main(void) {
    int rfd[NCHILD];
    pid_t pid[NCHILD];

    for (int i = 0; i < NCHILD; i++) {
        int fds[2];
        if (pipe(fds) != 0) {
            perror("pipe");
            return 1;
        }
        pid_t p = fork();
        if (p < 0) {
            perror("fork");
            return 1;
        }
        if (p == 0) {
            /* child: write MSGS records of its (id+1) byte, then exit id. */
            close(fds[0]);
            unsigned char rec[REC];
            memset(rec, (unsigned char)(i + 1), REC);
            for (int m = 0; m < MSGS; m++) {
                ssize_t off = 0;
                while (off < REC) {
                    ssize_t w = write(fds[1], rec + off, REC - off);
                    if (w < 0) {
                        if (errno == EINTR)
                            continue;
                        _exit(100);
                    }
                    off += w;
                }
            }
            close(fds[1]);
            _exit(i & 0x7f);
        }
        /* parent */
        close(fds[1]);
        rfd[i] = fds[0];
        pid[i] = p;
    }

    uint64_t checksum = 0;   /* order-independent */
    uint64_t orderdig = 1469598103934665603ULL; /* FNV-1a over arrival ids */
    unsigned char head[64];
    int head_n = 0;
    long arrivals = 0;

    int open_pipes = NCHILD;
    struct pollfd pfd[NCHILD];
    while (open_pipes > 0) {
        int n = 0;
        int idx[NCHILD];
        for (int i = 0; i < NCHILD; i++) {
            if (rfd[i] >= 0) {
                pfd[n].fd = rfd[i];
                pfd[n].events = POLLIN;
                pfd[n].revents = 0;
                idx[n] = i;
                n++;
            }
        }
        int r = poll(pfd, n, -1);
        if (r < 0) {
            if (errno == EINTR)
                continue;
            perror("poll");
            return 1;
        }
        for (int j = 0; j < n; j++) {
            if (!(pfd[j].revents & (POLLIN | POLLHUP)))
                continue;
            int i = idx[j];
            unsigned char buf[256];
            ssize_t got = read(rfd[i], buf, sizeof buf);
            if (got < 0) {
                if (errno == EINTR)
                    continue;
                perror("read");
                return 1;
            }
            if (got == 0) {
                close(rfd[i]);
                rfd[i] = -1;
                open_pipes--;
                continue;
            }
            for (ssize_t b = 0; b < got; b++)
                checksum += buf[b];
            /* one "arrival" event per read, tagged by child id */
            orderdig ^= (uint64_t)(i + 1);
            orderdig *= 1099511628211ULL;
            orderdig ^= (uint64_t)got;
            orderdig *= 1099511628211ULL;
            if (head_n < (int)sizeof head)
                head[head_n++] = (unsigned char)(i + 1);
            arrivals++;
        }
    }

    int reaped = 0;
    uint64_t statushash = 1469598103934665603ULL;
    for (int i = 0; i < NCHILD; i++) {
        int st = 0;
        pid_t w = waitpid(pid[i], &st, 0);
        if (w < 0) {
            perror("waitpid");
            return 1;
        }
        reaped++;
        if (WIFEXITED(st)) {
            statushash ^= (uint64_t)WEXITSTATUS(st);
            statushash *= 1099511628211ULL;
        }
    }

    printf("FORKPIPE_CHILDREN %d\n", NCHILD);
    printf("FORKPIPE_REAPED %d\n", reaped);
    printf("FORKPIPE_ARRIVALS %ld\n", arrivals);
    printf("FORKPIPE_CHECKSUM %llu\n", (unsigned long long)checksum);
    printf("FORKPIPE_STATUSHASH %llu\n", (unsigned long long)statushash);
    printf("FORKPIPE_ORDERDIG %llu\n", (unsigned long long)orderdig);
    printf("FORKPIPE_ORDERHEAD ");
    for (int k = 0; k < head_n; k++)
        printf("%d ", head[k]);
    printf("\n");
    printf("FORKPIPE_DONE\n");
    fflush(stdout);
    return 0;
}
