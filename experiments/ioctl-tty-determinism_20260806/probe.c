/* ioctl / tty determinism probe.
 *
 * Prints one `key=value` line per guest-visible tty fact, in a fixed order, so
 * two runs can be compared byte-for-byte. Everything printed is a value the
 * guest can branch on. No addresses, no pids, no timestamps: any difference
 * between two runs of the same configuration is a determinism defect.
 *
 * Build: gcc -O0 -static -o probe probe.c
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

static const char *fdname(int fd)
{
    switch (fd) {
    case 0: return "stdin";
    case 1: return "stdout";
    case 2: return "stderr";
    default: return "other";
    }
}

/* isatty(3) is just TCGETS under the hood; report it separately anyway because
 * that is the call real programs make. */
static void probe_isatty(int fd)
{
    errno = 0;
    int r = isatty(fd);
    printf("isatty.%s=%d errno=%d\n", fdname(fd), r, r ? 0 : errno);
}

static void probe_winsize(int fd)
{
    struct winsize ws;
    memset(&ws, 0, sizeof ws);
    errno = 0;
    int r = ioctl(fd, TIOCGWINSZ, &ws);
    if (r == 0)
        printf("TIOCGWINSZ.%s=ok rows=%u cols=%u xpixel=%u ypixel=%u\n",
               fdname(fd), ws.ws_row, ws.ws_col, ws.ws_xpixel, ws.ws_ypixel);
    else
        printf("TIOCGWINSZ.%s=err errno=%d\n", fdname(fd), errno);
}

static void probe_termios(int fd)
{
    struct termios t;
    memset(&t, 0, sizeof t);
    errno = 0;
    int r = tcgetattr(fd, &t); /* TCGETS */
    if (r != 0) {
        printf("TCGETS.%s=err errno=%d\n", fdname(fd), errno);
        return;
    }
    printf("TCGETS.%s=ok iflag=%08lx oflag=%08lx cflag=%08lx lflag=%08lx\n",
           fdname(fd), (unsigned long)t.c_iflag, (unsigned long)t.c_oflag,
           (unsigned long)t.c_cflag, (unsigned long)t.c_lflag);
    printf("TCGETS.%s.line=%u ispeed=%lu ospeed=%lu\n", fdname(fd),
           (unsigned)t.c_line, (unsigned long)cfgetispeed(&t),
           (unsigned long)cfgetospeed(&t));
    /* Every control character: a guest can branch on any of them. */
    printf("TCGETS.%s.cc=", fdname(fd));
    for (size_t i = 0; i < NCCS; i++)
        printf("%02x", (unsigned)t.c_cc[i]);
    printf("\n");
}

/* Job-control / session ioctls: these return host pids when not virtualized. */
static void probe_jobctl(int fd)
{
    int pgrp = 0;
    errno = 0;
    if (ioctl(fd, TIOCGPGRP, &pgrp) == 0)
        printf("TIOCGPGRP.%s=ok pgrp=%d\n", fdname(fd), pgrp);
    else
        printf("TIOCGPGRP.%s=err errno=%d\n", fdname(fd), errno);

    int sid = 0;
    errno = 0;
    if (ioctl(fd, TIOCGSID, &sid) == 0)
        printf("TIOCGSID.%s=ok sid=%d\n", fdname(fd), sid);
    else
        printf("TIOCGSID.%s=err errno=%d\n", fdname(fd), errno);
}

/* Queue-depth ioctls: host-timing dependent when they pass through. */
static void probe_queues(int fd)
{
    int n = -1;
    errno = 0;
    if (ioctl(fd, FIONREAD, &n) == 0)
        printf("FIONREAD.%s=ok n=%d\n", fdname(fd), n);
    else
        printf("FIONREAD.%s=err errno=%d\n", fdname(fd), errno);

    n = -1;
    errno = 0;
    if (ioctl(fd, TIOCOUTQ, &n) == 0)
        printf("TIOCOUTQ.%s=ok n=%d\n", fdname(fd), n);
    else
        printf("TIOCOUTQ.%s=err errno=%d\n", fdname(fd), errno);
}

static void probe_ttyname(int fd)
{
    char buf[256];
    errno = 0;
    int r = ttyname_r(fd, buf, sizeof buf);
    if (r == 0)
        printf("ttyname.%s=%s\n", fdname(fd), buf);
    else
        printf("ttyname.%s=err errno=%d\n", fdname(fd), r);
}

int main(void)
{
    /* Line-buffer-free: one write per line would interleave differently under
     * different schedulers. Use a full buffer and one flush at exit so the
     * byte stream is a function of the values, not of the write pattern. */
    static char obuf[1 << 16];
    setvbuf(stdout, obuf, _IOFBF, sizeof obuf);

    printf("probe=ioctl-tty v=1\n");
    for (int fd = 0; fd <= 2; fd++) {
        probe_isatty(fd);
        probe_winsize(fd);
        probe_termios(fd);
        probe_jobctl(fd);
        probe_queues(fd);
        probe_ttyname(fd);
    }

    /* /dev/tty is the controlling terminal regardless of redirection: a
     * separate reachability question from fd 0/1/2. */
    errno = 0;
    int t = open("/dev/tty", O_RDONLY);
    if (t < 0) {
        printf("devtty=err errno=%d\n", errno);
    } else {
        struct winsize ws;
        memset(&ws, 0, sizeof ws);
        errno = 0;
        if (ioctl(t, TIOCGWINSZ, &ws) == 0)
            printf("devtty=ok rows=%u cols=%u\n", ws.ws_row, ws.ws_col);
        else
            printf("devtty=open-ok winsz-err errno=%d\n", errno);
        close(t);
    }

    fflush(stdout);
    return 0;
}
