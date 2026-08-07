/* pipe-wakeup-probe.c -- isolate WHICH pipe edge hermit fails to deliver.
 *
 * hermit#1850: guests hang forever at zero CPU waiting on a pipe. Two shapes
 * were observed (cmake in epoll_wait(timeout=-1), stdenv fixupPhase bash in
 * read()), and "one root cause" was only ever a hypothesis. These probes
 * separate the candidate failing edges, because they need different fixes:
 *
 *   eof-read      blocking read() must return 0 when the last writer closes
 *   eof-epoll     epoll_wait must report EPOLLHUP (or EPOLLIN w/ 0 bytes)
 *   data-read     blocking read() must return data a dead child already wrote
 *   data-epoll    epoll_wait must report EPOLLIN for that buffered data
 *   eof-poll      poll() must report POLLHUP  (different kernel path to epoll)
 *   writer-alive  CONTROL: parent keeps its own write end open, so NO EOF is
 *                 expected natively either. If this "hangs" everywhere, a hang
 *                 in the others cannot be blamed on hermit without this control.
 *
 * Each probe alarm(N)s itself, so a hang exits 3 (TIMEOUT) instead of wedging.
 * Usage: pipe-wakeup-probe <name> [timeout_s]
 */
#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/wait.h>
#include <unistd.h>

static const char *g_name = "?";
static void on_alarm(int sig) {
    (void)sig;
    /* write(2) is async-signal-safe; printf is not. */
    char buf[128];
    int n = snprintf(buf, sizeof buf, "%s: TIMEOUT (hang)\n", g_name);
    ssize_t w = write(2, buf, n); (void)w;
    _exit(3);
}

/* Fork a child that optionally writes one byte, then exits. Returns read fd.
 * keep_write: if nonzero the PARENT keeps its own write end open (control). */
static int spawn(int write_byte, int keep_write, int *saved_w) {
    int fds[2];
    if (pipe(fds) != 0) { perror("pipe"); _exit(4); }
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); _exit(4); }
    if (pid == 0) {
        close(fds[0]);
        if (write_byte) { ssize_t w = write(fds[1], "x", 1); (void)w; }
        close(fds[1]);
        _exit(0);
    }
    if (keep_write) { *saved_w = fds[1]; } else { close(fds[1]); *saved_w = -1; }
    int status = 0;
    waitpid(pid, &status, 0);   /* writer is definitively gone before we wait */
    return fds[0];
}

static int do_read(int fd, const char *what) {
    char buf[8];
    ssize_t n = read(fd, buf, sizeof buf);
    if (n < 0) { printf("%s: read ERRNO %d (%s)\n", g_name, errno, strerror(errno)); return 1; }
    printf("%s: read returned %zd (%s)\n", g_name, n, what);
    return 0;
}

static int do_epoll(int fd) {
    int ep = epoll_create1(0);
    if (ep < 0) { perror("epoll_create1"); return 4; }
    struct epoll_event ev = {.events = EPOLLIN, .data.fd = fd};
    if (epoll_ctl(ep, EPOLL_CTL_ADD, fd, &ev) != 0) { perror("epoll_ctl"); return 4; }
    struct epoll_event out[4];
    int n = epoll_wait(ep, out, 4, -1);          /* INFINITE, exactly as cmake does */
    if (n < 0) { printf("%s: epoll_wait ERRNO %d (%s)\n", g_name, errno, strerror(errno)); return 1; }
    printf("%s: epoll_wait returned %d events=0x%x%s%s%s\n", g_name, n,
           n > 0 ? out[0].events : 0,
           (n > 0 && (out[0].events & EPOLLIN))  ? " EPOLLIN"  : "",
           (n > 0 && (out[0].events & EPOLLHUP)) ? " EPOLLHUP" : "",
           (n > 0 && (out[0].events & EPOLLERR)) ? " EPOLLERR" : "");
    return 0;
}

static int do_poll(int fd) {
    struct pollfd pfd = {.fd = fd, .events = POLLIN};
    int n = poll(&pfd, 1, -1);
    if (n < 0) { printf("%s: poll ERRNO %d (%s)\n", g_name, errno, strerror(errno)); return 1; }
    printf("%s: poll returned %d revents=0x%x%s%s\n", g_name, n, pfd.revents,
           (pfd.revents & POLLIN) ? " POLLIN" : "", (pfd.revents & POLLHUP) ? " POLLHUP" : "");
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <probe> [timeout_s]\n", argv[0]); return 64; }
    g_name = argv[1];
    unsigned t = (argc > 2) ? (unsigned)atoi(argv[2]) : 10;
    signal(SIGALRM, on_alarm);
    alarm(t);
    setvbuf(stdout, NULL, _IONBF, 0);

    int saved_w = -1, fd;
    if (!strcmp(g_name, "eof-read"))     { fd = spawn(0, 0, &saved_w); return do_read(fd, "0 == EOF, correct"); }
    if (!strcmp(g_name, "eof-epoll"))    { fd = spawn(0, 0, &saved_w); return do_epoll(fd); }
    if (!strcmp(g_name, "eof-poll"))     { fd = spawn(0, 0, &saved_w); return do_poll(fd); }
    if (!strcmp(g_name, "data-read"))    { fd = spawn(1, 0, &saved_w); return do_read(fd, "1 == data, correct"); }
    if (!strcmp(g_name, "data-epoll"))   { fd = spawn(1, 0, &saved_w); return do_epoll(fd); }
    if (!strcmp(g_name, "writer-alive")) { fd = spawn(0, 1, &saved_w); return do_read(fd, "CONTROL: a hang here is CORRECT"); }
    fprintf(stderr, "unknown probe %s\n", g_name);
    return 64;
}
