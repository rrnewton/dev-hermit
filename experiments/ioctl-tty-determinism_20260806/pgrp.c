/* Is TIOCGPGRP/TIOCGSID consistent with what the guest believes its own
 * process group and session are?
 *
 * On real Linux, for a foreground job on its controlling terminal,
 * ioctl(TIOCGPGRP) == getpgrp() and ioctl(TIOCGSID) == getsid(0). A
 * determinism engine that virtualizes pids must translate the ioctl results
 * too, or the guest sees two contradictory answers to the same question.
 *
 * Build: gcc -O0 -o pgrp pgrp.c
 */
#include <stdio.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

int main(void)
{
    int tpgrp = -1, tsid = -1;
    int rp = ioctl(1, TIOCGPGRP, &tpgrp);
    int rs = ioctl(1, TIOCGSID, &tsid);
    printf("getpid=%d getpgrp=%d getsid=%d\n",
           (int)getpid(), (int)getpgrp(), (int)getsid(0));
    printf("TIOCGPGRP=%s val=%d   TIOCGSID=%s val=%d\n",
           rp == 0 ? "ok" : "err", tpgrp, rs == 0 ? "ok" : "err", tsid);
    printf("consistent_pgrp=%s consistent_sid=%s\n",
           (rp == 0 && tpgrp == (int)getpgrp()) ? "yes" : "NO",
           (rs == 0 && tsid == (int)getsid(0)) ? "yes" : "NO");
    return 0;
}
