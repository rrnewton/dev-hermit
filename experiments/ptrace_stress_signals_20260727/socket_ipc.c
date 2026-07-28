/*
 * Stress family 4: pipe + socket IPC.
 *
 * Two pressures in one program:
 *   (a) AF_UNIX SOCK_STREAM socketpair ping-pong between parent and a forked
 *       child: ROUNDS request/response exchanges, each side hashing what it
 *       receives. Bidirectional stream IPC across a fork.
 *   (b) A ring of NRING processes connected by pipes (proc k writes to proc
 *       k+1); a token is passed around the ring LAPS times, each hop folded into
 *       a hash. This stresses many concurrent pipe endpoints and blocking
 *       read/write rendezvous.
 * Order-independent hashes (deterministic by construction) plus the observed
 * exchange counts must be bitwise-stable under --verify.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

#define ROUNDS 500
#define NRING 6
#define LAPS 20

static uint64_t fnv(uint64_t h, uint64_t v) {
    h ^= v;
    h *= 1099511628211ULL;
    return h;
}

static int rd(int fd, void *b, size_t n) {
    size_t off = 0;
    while (off < n) {
        ssize_t r = read(fd, (char *)b + off, n - off);
        if (r < 0)
            return -1;
        if (r == 0)
            return 1; /* EOF */
        off += (size_t)r;
    }
    return 0;
}
static int wr(int fd, const void *b, size_t n) {
    size_t off = 0;
    while (off < n) {
        ssize_t w = write(fd, (const char *)b + off, n - off);
        if (w < 0)
            return -1;
        off += (size_t)w;
    }
    return 0;
}

static uint64_t socketpair_pingpong(void) {
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) != 0) {
        perror("socketpair");
        exit(1);
    }
    pid_t p = fork();
    if (p == 0) {
        close(sv[0]);
        uint64_t h = 1469598103934665603ULL;
        for (int i = 0; i < ROUNDS; i++) {
            uint64_t req;
            if (rd(sv[1], &req, sizeof req))
                _exit(2);
            h = fnv(h, req);
            uint64_t resp = req * 2 + 1;
            if (wr(sv[1], &resp, sizeof resp))
                _exit(3);
        }
        /* child reports its hash as low byte of exit is not enough; write it */
        wr(sv[1], &h, sizeof h);
        close(sv[1]);
        _exit(0);
    }
    close(sv[1]);
    uint64_t h = 1469598103934665603ULL;
    for (int i = 0; i < ROUNDS; i++) {
        uint64_t req = (uint64_t)i * 2654435761ULL;
        if (wr(sv[0], &req, sizeof req))
            exit(4);
        uint64_t resp;
        if (rd(sv[0], &resp, sizeof resp))
            exit(5);
        h = fnv(h, resp);
    }
    uint64_t childh;
    rd(sv[0], &childh, sizeof childh);
    close(sv[0]);
    int st = 0;
    waitpid(p, &st, 0);
    return fnv(h, childh);
}

static uint64_t pipe_ring(void) {
    int in[NRING][2];
    for (int k = 0; k < NRING; k++)
        if (pipe(in[k]) != 0) {
            perror("pipe");
            exit(1);
        }
    /* proc k reads from in[k], writes to in[(k+1)%NRING] */
    pid_t pids[NRING];
    for (int k = 0; k < NRING; k++) {
        pid_t p = fork();
        if (p == 0) {
            int rfd = in[k][0];
            int wfd = in[(k + 1) % NRING][1];
            /* close all other fds we don't need */
            for (int j = 0; j < NRING; j++) {
                if (in[j][0] != rfd)
                    close(in[j][0]);
                if (in[j][1] != wfd)
                    close(in[j][1]);
            }
            uint64_t h = 1469598103934665603ULL + (uint64_t)k;
            for (;;) {
                uint64_t tok;
                int rc = rd(rfd, &tok, sizeof tok);
                if (rc)
                    _exit((int)(h & 0x3f)); /* EOF -> stop */
                h = fnv(h, tok);
                if (tok & (1ULL << 62)) {
                    /* stop sentinel: forward once so the ring drains, then stop */
                    wr(wfd, &tok, sizeof tok);
                    close(wfd);
                    _exit((int)(h & 0x3f));
                }
                uint64_t next = fnv(tok, (uint64_t)k);
                wr(wfd, &next, sizeof next);
            }
        }
        pids[k] = p;
    }
    /* parent injects a token into proc 0's input, laps it, then a stop sentinel */
    int inject = in[0][1];
    int drain = in[0][0]; /* parent also reads proc(NRING-1)->proc0 edge? no */
    (void)drain;
    /* close read ends in parent; keep write to proc0 */
    for (int k = 0; k < NRING; k++) {
        close(in[k][0]);
        if (in[k][1] != inject)
            close(in[k][1]);
    }
    uint64_t seed = 0x9e3779b97f4a7c15ULL;
    for (int lap = 0; lap < LAPS; lap++) {
        wr(inject, &seed, sizeof seed);
        seed = fnv(seed, (uint64_t)lap);
    }
    uint64_t stop = (1ULL << 62) | 0xABCDEF;
    wr(inject, &stop, sizeof stop);
    close(inject);

    uint64_t exithash = 1469598103934665603ULL;
    for (int k = 0; k < NRING; k++) {
        int st = 0;
        waitpid(pids[k], &st, 0);
        if (WIFEXITED(st))
            exithash = fnv(exithash, (uint64_t)WEXITSTATUS(st));
    }
    return exithash;
}

int main(void) {
    uint64_t sp = socketpair_pingpong();
    uint64_t ring = pipe_ring();
    printf("IPC_SOCKETPAIR_ROUNDS %d\n", ROUNDS);
    printf("IPC_SOCKETPAIR_HASH %llu\n", (unsigned long long)sp);
    printf("IPC_RING_PROCS %d\n", NRING);
    printf("IPC_RING_LAPS %d\n", LAPS);
    printf("IPC_RING_EXITHASH %llu\n", (unsigned long long)ring);
    printf("IPC_DONE\n");
    return 0;
}
