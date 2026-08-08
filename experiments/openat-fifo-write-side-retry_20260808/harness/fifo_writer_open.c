// Minimal reproducer for defect-class instance 3 (write side).
// Parent forks a reader; the writer opens the FIFO O_WRONLY, which blocks in
// the kernel at wait_for_partner until the reader opens it. Under Hermit's
// sequentializing scheduler the writer holds the turn while blocked, so the
// reader can never be scheduled to unblock it -> deadlock.
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <string.h>

int main(void) {
    const char *path = "./fifo-repro-f";
    unlink(path);
    if (mkfifo(path, 0600) != 0) { perror("mkfifo"); return 1; }
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 1; }
    if (pid == 0) {                      // child: the reader (the partner)
        int rfd = open(path, O_RDONLY);
        if (rfd < 0) { perror("child open"); _exit(1); }
        char buf[16]; ssize_t n = read(rfd, buf, sizeof buf);
        _exit(n >= 0 ? 0 : 1);
    }
    int wfd = open(path, O_WRONLY);      // <-- blocks at wait_for_partner
    if (wfd < 0) { perror("parent open"); return 1; }
    if (write(wfd, "ok", 2) != 2) { perror("write"); return 1; }
    close(wfd);
    int st = 0; waitpid(pid, &st, 0);
    printf("FIFO-OK exit=%d\n", WEXITSTATUS(st));
    return 0;
}
