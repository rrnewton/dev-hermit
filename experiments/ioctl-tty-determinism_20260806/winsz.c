/* Minimal TIOCGWINSZ guests, used to ask whether hermit's OWN parity gate can
 * SEE the leak, as opposed to merely being downstream of it.
 *
 *   winsz silent   read the host terminal geometry into guest memory and exit 0
 *                  without ever printing or branching on it. Guest-visible
 *                  output and exit status are constant by construction; the
 *                  only difference between two runs at different host terminal
 *                  sizes is the bytes sitting in guest memory.
 *
 *   winsz branch   the same read, but the guest branches on it (exit status
 *                  carries the column count). This is the shape of every real
 *                  program that formats to the terminal width.
 *
 * If a parity gate passes `silent` across two different host geometries, that
 * gate is BLIND to the leak: the host state is in the guest, the gate just
 * cannot see it until the guest acts on it.
 *
 * Build: gcc -O0 -o winsz winsz.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    int branch = (argc > 1 && strcmp(argv[1], "branch") == 0);
    /* Optional second arg selects the fd, so the same guest can ask "is THIS
     * descriptor still a terminal?" of stdin, stdout, and stderr separately. */
    int fd = (argc > 2) ? atoi(argv[2]) : 1;
    struct winsize ws;
    memset(&ws, 0, sizeof ws);

    if (ioctl(fd, TIOCGWINSZ, &ws) != 0)
        return 70; /* not a tty: distinguishable from every geometry below */

    if (!branch)
        return 0; /* silent: host geometry is in guest memory, unobserved */

    /* branch: make the leaked value guest-visible, the way a real program does */
    return ws.ws_col % 200;
}
