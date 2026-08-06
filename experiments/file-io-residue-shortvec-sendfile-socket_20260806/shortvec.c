/* File-I/O determinism RESIDUE: the two shapes the parent file-io sweep did NOT exercise.
 *
 * PARENT SWEEP'S GAP, restated precisely so the modes below can be read against it:
 *   - its `readvwritev` mode ran readv/writev on a REGULAR FILE. A regular-file readv is
 *     short only at EOF, so the split point is a function of file size -- it can never be
 *     host- or timing-dependent. The interesting case is a readv whose return is short
 *     because of how much data HAPPENED to be available, which puts the split point
 *     INSIDE the iovec array. That is "short-vector" and it was never run.
 *   - its `sendfile` mode ran FILE -> FILE. A file-to-file sendfile transfers everything.
 *     sendfile to a SOCKET stops when the socket send buffer fills, so the return count is
 *     a function of buffer state and reader drain timing. That was never run either.
 *
 * DESIGN RULE INHERITED FROM THE PARENT (and it is the reason it found anything): print
 * the RETURN LENGTHS and the SPLIT STRUCTURE, never just the totals. A run that moved the
 * same bytes via a different split is a DIVERGENT run that a total-only check scores clean.
 *
 * SECOND RULE, ADDED HERE: for a vectored op the return count alone does not say where the
 * split landed relative to the iovec boundaries -- so each mode also prints the PER-ELEMENT
 * FILL DEPTH, recovered by pre-poisoning every buffer with a sentinel and counting how far
 * the poison survived. That is the observable a program branching on iovec state would see.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <pthread.h>
#include <limits.h>
#include <sys/uio.h>
#include <sys/sendfile.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#define POISON 0xA5

/* FNV-1a over transferred bytes: proves the DATA is right, independent of the split. */
static unsigned long fnv(const unsigned char *p, size_t n, unsigned long h) {
    for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211UL; }
    return h;
}

/* How many leading bytes of this buffer are no longer POISON. This is the per-element
 * fill depth -- the thing that tells you WHERE in the vector the short count landed. */
static size_t filled(const unsigned char *b, size_t n) {
    size_t k = 0;
    while (k < n && b[k] != POISON) k++;
    return k;
}

static void poison(struct iovec *iov, int n) {
    for (int i = 0; i < n; i++) memset(iov[i].iov_base, POISON, iov[i].iov_len);
}

/* Print return count AND the per-element fill vector. */
static void report_iov(const char *tag, int i, ssize_t r, struct iovec *iov, int n) {
    printf("%s[%d] ret=%zd fill=", tag, i, r);
    for (int k = 0; k < n; k++)
        printf("%s%zu", k ? "," : "", filled(iov[k].iov_base, iov[k].iov_len));
    printf("\n");
}

static int make_file(const char *path, size_t bytes) {
    int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) return -1;
    unsigned char *b = malloc(bytes);
    /* content deliberately avoids POISON (0xA5): filled() detects the sentinel, so a
     * legitimate 0xA5 in the payload would silently understate the fill depth. */
    for (size_t i = 0; i < bytes; i++) b[i] = (unsigned char)((i * 7 + 13) % 0xA0);
    ssize_t w = write(fd, b, bytes);
    free(b);
    if (w != (ssize_t)bytes) { close(fd); return -1; }
    lseek(fd, 0, SEEK_SET);
    return fd;
}

/* ------------------------------------------------------------------ pipe writer */
static int pfd[2];
static int wr_chunk = 4096, wr_reps = 48;
static void *pipe_writer(void *a) {
    (void)a;
    unsigned char *chunk = malloc(wr_chunk);
    for (int i = 0; i < wr_reps; i++) {
        memset(chunk, (unsigned char)('A' + (i % 26)), wr_chunk);
        ssize_t w = write(pfd[1], chunk, wr_chunk);
        (void)w;
    }
    free(chunk);
    close(pfd[1]);
    return NULL;
}

/* ------------------------------------------------------- socket drain helper */
struct drain_arg { int fd; long total; unsigned long hash; int chunk; };
static void *drainer(void *v) {
    struct drain_arg *d = v;
    unsigned char *b = malloc(d->chunk);
    ssize_t r;
    d->hash = 1469598103934665603UL;
    while ((r = read(d->fd, b, d->chunk)) > 0) { d->total += r; d->hash = fnv(b, r, d->hash); }
    free(b);
    return NULL;
}

int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "readv-pipe-short";

    /* ============================ A. readv/writev SHORT-VECTOR ==================== */

    if (!strcmp(mode, "readv-pipe-short")) {
        /* THE core uncovered case. A pipe delivers only what is currently buffered, so a
         * readv over a 4-element / 24KiB vector returns a count driven by writer timing
         * and the split lands at an arbitrary point INSIDE the vector. */
        if (pipe(pfd)) { perror("pipe"); return 1; }
        pthread_t t; pthread_create(&t, NULL, pipe_writer, NULL);
        /* 1KiB+4KiB+16KiB+192KiB = 213KiB, well ABOVE the 64KiB pipe capacity. That is
         * the whole point: if the vector fit inside the pipe buffer the readv would
         * always be satisfied in full and could never go short (measured: a 25KiB
         * vector returned 25600 on every call, i.e. a vacuous cell). */
        static unsigned char b0[1024], b1[4096], b2[16384], b3[196608];
        struct iovec iov[4] = {{b0,sizeof b0},{b1,sizeof b1},{b2,sizeof b2},{b3,sizeof b3}};
        ssize_t r; int n = 0; long total = 0; unsigned long h = 1469598103934665603UL;
        for (;;) {
            poison(iov, 4);
            r = readv(pfd[0], iov, 4);
            if (r <= 0) break;
            report_iov("readv", n++, r, iov, 4);
            total += r;
            /* hash the bytes actually delivered, in vector order */
            ssize_t left = r;
            for (int k = 0; k < 4 && left > 0; k++) {
                size_t take = (size_t)left < iov[k].iov_len ? (size_t)left : iov[k].iov_len;
                h = fnv(iov[k].iov_base, take, h);
                left -= take;
            }
        }
        pthread_join(t, NULL); close(pfd[0]);
        printf("readv-pipe-short total=%ld calls=%d hash=%lu err=%d\n",
               total, n, h, r < 0 ? errno : 0);

    } else if (!strcmp(mode, "readv-nonblock-short")) {
        /* Same shape but O_NONBLOCK, so the reader also observes EAGAIN. The EAGAIN COUNT
         * is itself an observable and is pure scheduling -- natively it should vary a lot. */
        /* O_NONBLOCK on the READ END ONLY. pipe2(O_NONBLOCK) sets BOTH ends, which makes
         * the writer's write() fail with EAGAIN and silently DROP data -- measured: the
         * byte total then varies for a reason that has nothing to do with the readv split,
         * which would have made this cell measure the wrong thing. */
        if (pipe(pfd)) { perror("pipe"); return 1; }
        fcntl(pfd[0], F_SETFL, O_NONBLOCK);
        pthread_t t; pthread_create(&t, NULL, pipe_writer, NULL);
        static unsigned char b0[512], b1[2048], b2[196608];   /* > pipe capacity */
        struct iovec iov[3] = {{b0,sizeof b0},{b1,sizeof b1},{b2,sizeof b2}};
        ssize_t r; int n = 0, eagain = 0; long total = 0;
        int done = 0;
        while (!done) {
            poison(iov, 3);
            r = readv(pfd[0], iov, 3);
            if (r > 0) { report_iov("readv", n++, r, iov, 3); total += r; }
            else if (r == 0) done = 1;
            else if (errno == EAGAIN) { eagain++; if (eagain > 200) done = 1; }
            else { printf("readv err=%d\n", errno); done = 1; }
        }
        pthread_join(t, NULL); close(pfd[0]);
        printf("readv-nonblock-short total=%ld calls=%d eagain=%d\n", total, n, eagain);

    } else if (!strcmp(mode, "writev-nonblock-short")) {
        /* Short WRITEV. On a blocking pipe writev never returns short (it blocks), so the
         * only way to observe the split is O_NONBLOCK: the vector is 192KiB against a
         * 64KiB pipe, so the return count exposes the capacity boundary mid-vector. */
        if (pipe2(pfd, O_NONBLOCK)) { perror("pipe2"); return 1; }
        static unsigned char b0[65536], b1[65536], b2[65536];
        memset(b0,'a',sizeof b0); memset(b1,'b',sizeof b1); memset(b2,'c',sizeof b2);
        struct iovec iov[3] = {{b0,sizeof b0},{b1,sizeof b1},{b2,sizeof b2}};
        ssize_t w; int n = 0, eagain = 0; long total = 0;
        for (int i = 0; i < 8; i++) {
            w = writev(pfd[1], iov, 3);
            if (w > 0) { printf("writev[%d] ret=%zd\n", n++, w); total += w; }
            else { eagain++; break; }
        }
        printf("writev-nonblock-short total=%ld calls=%d eagain=%d cap_probe=%ld\n",
               total, n, eagain, total);
        close(pfd[1]); close(pfd[0]);

    } else if (!strcmp(mode, "writev-drain-short")) {
        /* Short writev WITH a concurrent drainer: now the split point is a race between
         * writev and the reader, not a fixed capacity. This is the timing-dependent twin
         * of the mode above and is where a scheduler must impose an order. */
        if (pipe2(pfd, O_NONBLOCK)) { perror("pipe2"); return 1; }
        struct drain_arg d = { pfd[0], 0, 0, 8192 };
        /* make the read end blocking for the drainer */
        fcntl(pfd[0], F_SETFL, 0);
        pthread_t t; pthread_create(&t, NULL, drainer, &d);
        static unsigned char b0[65536], b1[65536], b2[65536];
        memset(b0,'a',sizeof b0); memset(b1,'b',sizeof b1); memset(b2,'c',sizeof b2);
        struct iovec iov[3] = {{b0,sizeof b0},{b1,sizeof b1},{b2,sizeof b2}};
        ssize_t w; int n = 0, eagain = 0; long total = 0;
        while (total < 3L*65536*4) {
            w = writev(pfd[1], iov, 3);
            if (w > 0) { printf("writev[%d] ret=%zd\n", n++, w); total += w; }
            else if (errno == EAGAIN) { if (++eagain > 200) break; }
            else break;
        }
        close(pfd[1]);
        pthread_join(t, NULL); close(pfd[0]);
        printf("writev-drain-short wrote=%ld calls=%d eagain=%d read=%ld hash=%lu\n",
               total, n, eagain, d.total, d.hash);

    } else if (!strcmp(mode, "readv-eof-short")) {
        /* The BOUNDARY short-vector: a regular-file readv that runs off EOF mid-vector.
         * Natively deterministic, so a green here is weak as determinism evidence -- it is
         * included as a CORRECTNESS check that the short count and the per-element fill
         * are right, which is a different question from whether they are stable. */
        int fd = make_file("hermit-sv-eof.tmp", 9000);
        unsigned char b0[1000], b1[3000], b2[9000];
        struct iovec iov[3] = {{b0,sizeof b0},{b1,sizeof b1},{b2,sizeof b2}};
        for (int i = 0; i < 3; i++) {
            poison(iov, 3);
            ssize_t r = readv(fd, iov, 3);
            printf("readv[%d] ret=%zd off=%lld fill=%zu,%zu,%zu\n", i, r,
                   (long long)lseek(fd, 0, SEEK_CUR),
                   filled(b0,sizeof b0), filled(b1,sizeof b1), filled(b2,sizeof b2));
        }
        /* preadv must not move the offset, and must be short at EOF too */
        poison(iov, 3);
        ssize_t pr = preadv(fd, iov, 3, 8000);
        printf("preadv ret=%zd off_after=%lld fill=%zu,%zu,%zu\n", pr,
               (long long)lseek(fd, 0, SEEK_CUR),
               filled(b0,sizeof b0), filled(b1,sizeof b1), filled(b2,sizeof b2));
        close(fd); unlink("hermit-sv-eof.tmp");

    } else if (!strcmp(mode, "iov-edge")) {
        /* Vector-shape edge cases: empty vector, zero-length elements interleaved with
         * real ones (the split must skip them), IOV_MAX exactly, IOV_MAX+1 (EINVAL). */
        int fd = make_file("hermit-sv-edge.tmp", 4096);
        unsigned char b[4096];
        struct iovec z0 = { b, 0 }, z1 = { b + 100, 100 }, z2 = { b + 300, 0 }, z3 = { b + 400, 200 };
        struct iovec mix[4] = { z0, z1, z2, z3 };
        memset(b, POISON, sizeof b);
        ssize_t r = readv(fd, mix, 4);
        printf("edge zero-interleaved ret=%zd fill=%zu,%zu,%zu,%zu\n", r,
               filled(b,0), filled(b+100,100), filled(b+300,0), filled(b+400,200));
        errno = 0;
        r = readv(fd, mix, 0);
        printf("edge iovcnt0 ret=%zd errno=%d\n", r, r < 0 ? errno : 0);
        struct iovec *big = calloc(IOV_MAX + 1, sizeof *big);
        static unsigned char pool[IOV_MAX + 2];
        for (int i = 0; i <= IOV_MAX; i++) { big[i].iov_base = &pool[i]; big[i].iov_len = 1; }
        lseek(fd, 0, SEEK_SET);
        errno = 0; r = readv(fd, big, IOV_MAX);
        printf("edge iovmax(%d) ret=%zd errno=%d\n", IOV_MAX, r, r < 0 ? errno : 0);
        lseek(fd, 0, SEEK_SET);
        errno = 0; r = readv(fd, big, IOV_MAX + 1);
        printf("edge iovmax+1 ret=%zd errno=%d\n", r, r < 0 ? errno : 0);
        free(big);
        close(fd); unlink("hermit-sv-edge.tmp");

    /* ============================ B. sendfile -> SOCKET =========================== */

    } else if (!strcmp(mode, "sendfile-sock-nodrain")) {
        /* THE minimal socket-destination case. Nonblocking AF_UNIX socketpair, NOBODY
         * reading: sendfile stops when the socket send buffer fills, so the return count
         * is the buffer boundary rather than the file length. This is precisely the short
         * count that file->file sendfile can never produce. */
        int sv[2];
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) { perror("socketpair"); return 1; }
        fcntl(sv[0], F_SETFL, O_NONBLOCK);
        int in = make_file("hermit-sv-sf1.tmp", 1 << 20);
        if (in < 0) { perror("make_file"); return 1; }
        off_t off = 0; ssize_t s; int n = 0; long total = 0; int eagain = 0;
        for (int i = 0; i < 4; i++) {
            errno = 0;
            s = sendfile(sv[0], in, &off, 1 << 20);
            if (s > 0) { printf("sendfile[%d] ret=%zd off=%lld\n", n++, s, (long long)off); total += s; }
            else if (s < 0 && errno == EAGAIN) { eagain++; break; }
            else break;
        }
        printf("sendfile-sock-nodrain total=%ld calls=%d eagain=%d err=%d\n",
               total, n, eagain, s < 0 ? errno : 0);
        close(sv[0]); close(sv[1]); close(in); unlink("hermit-sv-sf1.tmp");

    } else if (!strcmp(mode, "sendfile-sock-unix")) {
        /* AF_UNIX socketpair WITH a concurrent drainer. Now every sendfile return count is
         * a race between the sender filling and the drainer emptying, which natively gives
         * a different split sequence on every run. The total and the data hash must still
         * be invariant -- so this separates "moved the right bytes" from "moved them the
         * same way", which is the whole point of the task. */
        int sv[2];
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) { perror("socketpair"); return 1; }
        /* NONBLOCKING SENDER + SMALL SNDBUF is what actually produces short counts. With a
         * blocking socket and a default (auto-tuned) buffer the kernel simply loops inside
         * sendfile and returns the whole 1 MiB in ONE call -- measured, and it makes the
         * cell vacuous. Nonblocking forces sendfile to return whatever fit. */
        int sb = 16384;
        setsockopt(sv[0], SOL_SOCKET, SO_SNDBUF, &sb, sizeof sb);
        setsockopt(sv[1], SOL_SOCKET, SO_RCVBUF, &sb, sizeof sb);
        fcntl(sv[0], F_SETFL, O_NONBLOCK);
        int in = make_file("hermit-sv-sf2.tmp", 1 << 20);
        if (in < 0) { perror("make_file"); return 1; }
        struct drain_arg d = { sv[1], 0, 0, 4096 };
        pthread_t t; pthread_create(&t, NULL, drainer, &d);
        off_t off = 0; ssize_t s = 0; int n = 0, eagain = 0; long total = 0;
        while (total < (1 << 20)) {
            errno = 0;
            s = sendfile(sv[0], in, &off, 1 << 20);
            if (s > 0) { printf("sendfile[%d] ret=%zd off=%lld\n", n++, s, (long long)off); total += s; }
            else if (s < 0 && errno == EAGAIN) { if (++eagain > 200) break; }
            else break;
        }
        printf("sendfile-sock-unix eagain=%d\n", eagain);
        shutdown(sv[0], SHUT_WR); close(sv[0]);
        pthread_join(t, NULL); close(sv[1]);
        printf("sendfile-sock-unix sent=%ld calls=%d recv=%ld hash=%lu err=%d\n",
               total, n, d.total, d.hash, s < 0 ? errno : 0);
        close(in); unlink("hermit-sv-sf2.tmp");

    } else if (!strcmp(mode, "sendfile-sock-tcp")) {
        /* Loopback TCP destination. Adds the kernel's TCP send/receive windows on top of
         * the drain race, which is the strongest natural source of split variation here. */
        int ln = socket(AF_INET, SOCK_STREAM, 0);
        if (ln < 0) { perror("socket"); return 1; }
        int one = 1; setsockopt(ln, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        struct sockaddr_in a; memset(&a, 0, sizeof a);
        a.sin_family = AF_INET; a.sin_addr.s_addr = htonl(INADDR_LOOPBACK); a.sin_port = 0;
        if (bind(ln, (struct sockaddr *)&a, sizeof a)) { perror("bind"); return 1; }
        socklen_t al = sizeof a;
        if (getsockname(ln, (struct sockaddr *)&a, &al)) { perror("getsockname"); return 1; }
        if (listen(ln, 1)) { perror("listen"); return 1; }
        int cl = socket(AF_INET, SOCK_STREAM, 0);
        if (connect(cl, (struct sockaddr *)&a, sizeof a)) { perror("connect"); return 1; }
        int sr = accept(ln, NULL, NULL);
        if (sr < 0) { perror("accept"); return 1; }
        int sb = 16384;
        setsockopt(cl, SOL_SOCKET, SO_SNDBUF, &sb, sizeof sb);
        setsockopt(sr, SOL_SOCKET, SO_RCVBUF, &sb, sizeof sb);
        fcntl(cl, F_SETFL, O_NONBLOCK);
        int in = make_file("hermit-sv-sf3.tmp", 1 << 20);
        struct drain_arg d = { sr, 0, 0, 4096 };
        pthread_t t; pthread_create(&t, NULL, drainer, &d);
        off_t off = 0; ssize_t s = 0; int n = 0, eagain = 0; long total = 0;
        while (total < (1 << 20)) {
            errno = 0;
            s = sendfile(cl, in, &off, 1 << 20);
            if (s > 0) { printf("sendfile[%d] ret=%zd off=%lld\n", n++, s, (long long)off); total += s; }
            else if (s < 0 && errno == EAGAIN) { if (++eagain > 200) break; }
            else break;
        }
        printf("sendfile-sock-tcp eagain=%d\n", eagain);
        shutdown(cl, SHUT_WR); close(cl);
        pthread_join(t, NULL); close(sr); close(ln);
        printf("sendfile-sock-tcp sent=%ld calls=%d recv=%ld hash=%lu err=%d\n",
               total, n, d.total, d.hash, s < 0 ? errno : 0);
        close(in); unlink("hermit-sv-sf3.tmp");

    } else if (!strcmp(mode, "sendfile-sock-fallback")) {
        /* Tests the ASSUMPTION WRITTEN INTO THE PRODUCT, not just the behaviour.
         * detcore/src/syscalls/files.rs refuses a socket destination with ENOSYS and the
         * comment justifies it: "return ENOSYS for those endpoint types so libc/application
         * fallbacks use Detcore's existing read/write handlers instead."  This mode is the
         * application that actually HAS that fallback -- sendfile, and on ENOSYS a
         * read/write copy loop -- so it measures whether the intended path is itself
         * deterministic and moves the right bytes. A guest WITHOUT the fallback (the three
         * modes above) is the other half of the bracket. */
        int sv[2];
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) { perror("socketpair"); return 1; }
        int sb = 16384;
        setsockopt(sv[0], SOL_SOCKET, SO_SNDBUF, &sb, sizeof sb);
        setsockopt(sv[1], SOL_SOCKET, SO_RCVBUF, &sb, sizeof sb);
        int in = make_file("hermit-sv-sf4.tmp", 1 << 20);
        if (in < 0) { perror("make_file"); return 1; }
        struct drain_arg d = { sv[1], 0, 0, 4096 };
        pthread_t t; pthread_create(&t, NULL, drainer, &d);
        off_t off = 0; long total = 0; int n = 0, fellback = 0;
        errno = 0;
        ssize_t s = sendfile(sv[0], in, &off, 1 << 20);
        if (s > 0) { printf("sendfile[%d] ret=%zd off=%lld\n", n++, s, (long long)off); total += s; }
        if (s < 0 && errno == ENOSYS) {
            fellback = 1;
            printf("sendfile ENOSYS -> falling back to read/write\n");
            unsigned char *b = malloc(65536); ssize_t r;
            lseek(in, 0, SEEK_SET);
            while ((r = read(in, b, 65536)) > 0) {
                /* The inner loop is REQUIRED, and finding that out was itself a result:
                 * a BLOCKING write() of 65536 to this AF_UNIX stream socket returns
                 * 32640 under hermit (= the 32768 doubled SNDBUF minus overhead) where
                 * natively it returns all 65536. Without this loop the copy silently
                 * drops the remainder -- measured sent=522240 of 1048576. Printing every
                 * partial write keeps the split visible instead of hiding it. */
                ssize_t done = 0;
                while (done < r) {
                    ssize_t w = write(sv[0], b + done, r - done);
                    printf("fallback[%d] r=%zd w=%zd\n", n++, r - done, w);
                    if (w <= 0) { done = -1; break; }
                    done += w; total += w;
                }
                if (done < 0) break;
            }
            free(b);
        }
        shutdown(sv[0], SHUT_WR); close(sv[0]);
        pthread_join(t, NULL); close(sv[1]);
        printf("sendfile-sock-fallback sent=%ld calls=%d fellback=%d recv=%ld hash=%lu\n",
               total, n, fellback, d.total, d.hash);
        close(in); unlink("hermit-sv-sf4.tmp");

    } else if (!strcmp(mode, "sock-write-short")) {
        /* Isolates the short-write observation that fell out of the fallback mode, with
         * NO sendfile involved: a BLOCKING write() of 64 KiB to an AF_UNIX SOCK_STREAM
         * socket whose SNDBUF is 16 KiB. Natively Linux's unix_stream_sendmsg blocks and
         * transfers the whole buffer; if hermit returns short instead, that is a
         * guest-visible semantic difference on a path that has nothing to do with
         * vectored I/O -- and it is exactly the read/write path the sendfile ENOSYS
         * refusal redirects applications ONTO. Print every partial count. */
        int sv[2];
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) { perror("socketpair"); return 1; }
        int sb = 16384;
        setsockopt(sv[0], SOL_SOCKET, SO_SNDBUF, &sb, sizeof sb);
        setsockopt(sv[1], SOL_SOCKET, SO_RCVBUF, &sb, sizeof sb);
        struct drain_arg d = { sv[1], 0, 0, 4096 };
        pthread_t t; pthread_create(&t, NULL, drainer, &d);
        static unsigned char b[65536];
        for (size_t i = 0; i < sizeof b; i++) b[i] = (unsigned char)((i * 3 + 1) % 0xA0);
        long total = 0; int n = 0;
        for (int i = 0; i < 4; i++) {
            ssize_t w = write(sv[0], b, sizeof b);
            printf("write[%d] asked=%zu ret=%zd\n", n++, sizeof b, w);
            if (w <= 0) break;
            total += w;
        }
        shutdown(sv[0], SHUT_WR); close(sv[0]);
        pthread_join(t, NULL); close(sv[1]);
        printf("sock-write-short wrote=%ld calls=%d recv=%ld hash=%lu\n",
               total, n, d.total, d.hash);

    } else if (!strcmp(mode, "pipe-write-short") || !strcmp(mode, "pipe-writev-block")
            || !strcmp(mode, "sock-writev-block")) {
        /* THE SHORT-VECTOR WRITE CASE, on a BLOCKING endpoint -- which is where the task's
         * question actually bites. On Linux a blocking write()/writev() to a pipe or a
         * stream socket transfers the WHOLE buffer (the kernel sleeps and resumes); it goes
         * short only on a signal. So if a count comes back short here, the guest sees a
         * value Linux would never produce, and for writev the shortfall lands INSIDE the
         * iovec array -- the exact shape the parent sweep never exercised, because it only
         * ever ran writev against a regular file.
         *
         * Three variants share this block so the pipe/socket and scalar/vectored axes are
         * separable rather than confounded:
         *   pipe-write-short   blocking pipe,   write()  of 256 KiB
         *   pipe-writev-block  blocking pipe,   writev() of 3 x 64 KiB
         *   sock-writev-block  blocking AF_UNIX socket, writev() of 3 x 64 KiB
         * A drainer thread is always present, so a correct blocking endpoint never stalls. */
        int wfd, rfd;
        int is_sock = !strcmp(mode, "sock-writev-block");
        if (is_sock) {
            int sv[2];
            if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv)) { perror("socketpair"); return 1; }
            int sb = 16384;
            setsockopt(sv[0], SOL_SOCKET, SO_SNDBUF, &sb, sizeof sb);
            setsockopt(sv[1], SOL_SOCKET, SO_RCVBUF, &sb, sizeof sb);
            wfd = sv[0]; rfd = sv[1];
        } else {
            if (pipe(pfd)) { perror("pipe"); return 1; }
            wfd = pfd[1]; rfd = pfd[0];
        }
        struct drain_arg d = { rfd, 0, 0, 4096 };
        pthread_t t; pthread_create(&t, NULL, drainer, &d);
        static unsigned char w0[65536], w1[65536], w2[65536], w3[65536];
        memset(w0,'p',sizeof w0); memset(w1,'q',sizeof w1);
        memset(w2,'r',sizeof w2); memset(w3,'s',sizeof w3);
        long total = 0; int n = 0;
        if (!strcmp(mode, "pipe-write-short")) {
            static unsigned char big[262144];
            memset(big, 'z', sizeof big);
            for (int i = 0; i < 2; i++) {
                ssize_t w = write(wfd, big, sizeof big);
                printf("write[%d] asked=%zu ret=%zd\n", n++, sizeof big, w);
                if (w <= 0) break;
                total += w;
            }
        } else {
            struct iovec iov[3] = {{w1,sizeof w1},{w2,sizeof w2},{w3,sizeof w3}};
            size_t want = sizeof w1 + sizeof w2 + sizeof w3;
            for (int i = 0; i < 3; i++) {
                ssize_t w = writev(wfd, iov, 3);
                /* elem= says WHICH iovec element the shortfall landed in and how deep --
                 * a count alone cannot distinguish "stopped at a boundary" from
                 * "stopped mid-element", and only the latter is a torn vector. */
                long e = w < 0 ? -1 : w / 65536, off = w < 0 ? -1 : w % 65536;
                printf("writev[%d] asked=%zu ret=%zd elem=%ld+%ld\n", n++, want, w, e, off);
                if (w <= 0) break;
                total += w;
            }
        }
        if (is_sock) shutdown(wfd, SHUT_WR);
        close(wfd);
        pthread_join(t, NULL); close(rfd);
        printf("%s wrote=%ld calls=%d recv=%ld hash=%lu\n", mode, total, n, d.total, d.hash);

    } else {
        fprintf(stderr, "unknown mode %s\n", mode);
        return 2;
    }
    fflush(stdout);
    return 0;
}
