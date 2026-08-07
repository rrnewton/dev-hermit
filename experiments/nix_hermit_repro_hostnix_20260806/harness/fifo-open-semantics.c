/* fifo-open-semantics.c -- measure what O_NONBLOCK actually does to a FIFO open,
 * because the read and write sides are NOT symmetric and the difference decides
 * whether hermit#1850 instance 3 can be fixed with a nonblocking retry loop or
 * needs BlockedPool modelling.
 *
 * Instance 3: a guest blocks in openat() on a FIFO at kernel wchan
 * `wait_for_partner` while holding the scheduler turn; the partner that would
 * release it is queued behind. Measured flags of the blocked open: 577 =
 * O_WRONLY|O_CREAT|O_TRUNC -- the WRITE side.
 *
 * Probes (each self-timeboxed with alarm(), so a hang exits 3):
 *   w-noreader     O_WRONLY|O_NONBLOCK, no reader        -> expect ENXIO
 *   w-withreader   O_WRONLY|O_NONBLOCK, reader present   -> expect success
 *   w-blocking     O_WRONLY (no flag), no reader         -> expect HANG (the bug)
 *   r-nowriter     O_RDONLY|O_NONBLOCK, no writer        -> expect success
 *   r-eof          ... then clear O_NONBLOCK and read()  -> 0 (EOF) or block?
 *                  THIS is the hazard: a blocking O_RDONLY open would have
 *                  waited for a writer, so a guest converted to O_NONBLOCK can
 *                  observe EOF where it should have waited.
 *   r-blocking     O_RDONLY (no flag), no writer         -> expect HANG
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static const char *g = "?";
static void on_alarm(int s){ (void)s; char b[96]; int n=snprintf(b,sizeof b,"%s: HANG (timed out)\n",g); ssize_t w=write(2,b,n);(void)w; _exit(3); }

int main(int argc, char **argv){
    if (argc < 3) { fprintf(stderr,"usage: %s <probe> <fifo> [timeout]\n", argv[0]); return 64; }
    g = argv[1];
    const char *fifo = argv[2];
    unsigned t = (argc>3)? (unsigned)atoi(argv[3]) : 5;
    signal(SIGALRM,on_alarm); alarm(t); setvbuf(stdout,NULL,_IONBF,0);
    unlink(fifo);
    if (mkfifo(fifo, 0600) != 0) { perror("mkfifo"); return 4; }

    if (!strcmp(g,"w-noreader")) {
        int fd = open(fifo, O_WRONLY|O_NONBLOCK);
        printf("%s: fd=%d errno=%d (%s)\n", g, fd, fd<0?errno:0, fd<0?strerror(errno):"ok");
        return 0;
    }
    if (!strcmp(g,"w-withreader")) {
        int rfd = open(fifo, O_RDONLY|O_NONBLOCK);           /* park a reader */
        int fd  = open(fifo, O_WRONLY|O_NONBLOCK);
        printf("%s: reader_fd=%d write_fd=%d errno=%d (%s)\n", g, rfd, fd,
               fd<0?errno:0, fd<0?strerror(errno):"ok");
        return 0;
    }
    if (!strcmp(g,"w-blocking")) {
        int fd = open(fifo, O_WRONLY);                        /* expected to hang */
        printf("%s: fd=%d (did NOT hang)\n", g, fd);
        return 0;
    }
    if (!strcmp(g,"r-nowriter")) {
        int fd = open(fifo, O_RDONLY|O_NONBLOCK);
        printf("%s: fd=%d errno=%d (%s)\n", g, fd, fd<0?errno:0, fd<0?strerror(errno):"ok");
        return 0;
    }
    if (!strcmp(g,"r-eof")) {
        int fd = open(fifo, O_RDONLY|O_NONBLOCK);
        if (fd < 0) { printf("%s: open failed %s\n", g, strerror(errno)); return 1; }
        int fl = fcntl(fd, F_GETFL);
        fcntl(fd, F_SETFL, fl & ~O_NONBLOCK);                 /* restore what the guest asked for */
        char buf[8];
        ssize_t n = read(fd, buf, sizeof buf);
        printf("%s: read=%zd errno=%d (%s)  <- 0 means SPURIOUS EOF, the hazard\n",
               g, n, n<0?errno:0, n<0?strerror(errno):"no error");
        return 0;
    }
    if (!strcmp(g,"r-blocking")) {
        int fd = open(fifo, O_RDONLY);                        /* expected to hang */
        printf("%s: fd=%d (did NOT hang)\n", g, fd);
        return 0;
    }
    fprintf(stderr,"unknown probe %s\n", g); return 64;
}
