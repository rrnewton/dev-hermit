/* syscall-dense, single-process, single-thread, deterministic.
 * Drives openat/write/pread/close churn -- the axis ptrace taxes per-syscall.
 * Backend-portable by construction: no fork, no exec, no threads. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#define FILES 64
#define ROUNDS 900
#define BUF 4096
int main(void) {
    char dir[] = "/tmp/hermit-churn-XXXXXX";
    if (!mkdtemp(dir)) { perror("mkdtemp"); return 1; }
    char path[256]; static char buf[BUF]; unsigned long acc = 0;
    for (int i = 0; i < BUF; i++) buf[i] = (char)(i * 31 + 7);
    for (int r = 0; r < ROUNDS; r++) {
        for (int f = 0; f < FILES; f++) {
            snprintf(path, sizeof path, "%s/f%d", dir, f);
            int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0600);
            if (fd < 0) { perror("open"); return 1; }
            if (write(fd, buf, BUF) != BUF) return 1;
            static char rb[BUF];
            if (pread(fd, rb, BUF, 0) != BUF) return 1;
            for (int k = 0; k < BUF; k += 512) acc += (unsigned char)rb[k];
            close(fd);
            unlink(path);
        }
    }
    rmdir(dir);
    printf("syscall_churn ok acc=%lu rounds=%d files=%d\n", acc, ROUNDS, FILES);
    return 0;
}
