/*
 * Guest fixture for the cross-backend env-block parity check.
 *
 * Prints the guest-visible environment through BOTH authorities that a guest
 * can actually observe it through, because a backend can equalise one without
 * equalising the other:
 *
 *   ENVIRON  -- the libc `environ` array (what getenv()/environ walks see).
 *   PROCENV  -- /proc/self/environ (the kernel's view of the original block
 *               laid out on the initial stack between mm->env_start/env_end).
 *
 * A scrub performed inside the guest process can remove an entry from the
 * first while leaving the second untouched, so the two are reported and
 * compared separately rather than collapsed into one "the env".
 *
 * Output is line-oriented and stable-ordered (emission order is the guest's
 * own order; the checker does not sort, because order is itself part of the
 * block).
 */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>

extern char **environ;

int main(void) {
    size_t environ_bytes = 0, environ_count = 0;
    for (char **e = environ; *e != NULL; ++e) {
        printf("ENVIRON\t%s\n", *e);
        environ_bytes += strlen(*e) + 1; /* +1 for the NUL that separates entries */
        environ_count++;
    }

    /* The kernel's copy. Read it whole; it is small and NUL-separated. */
    static char buffer[1 << 16];
    size_t procenv_bytes = 0, procenv_count = 0;
    int fd = open("/proc/self/environ", O_RDONLY);
    if (fd < 0) {
        printf("PROCENV_UNAVAILABLE\topen failed\n");
    } else {
        ssize_t total = 0, got;
        while ((got = read(fd, buffer + total, sizeof(buffer) - (size_t)total)) > 0) {
            total += got;
            if ((size_t)total == sizeof(buffer)) break;
        }
        close(fd);
        if (total < 0) {
            printf("PROCENV_UNAVAILABLE\tread failed\n");
        } else {
            procenv_bytes = (size_t)total;
            for (ssize_t i = 0; i < total; ) {
                const char *entry = buffer + i;
                size_t len = strnlen(entry, (size_t)(total - i));
                if (len == 0) { i++; continue; }
                printf("PROCENV\t%s\n", entry);
                procenv_count++;
                i += (ssize_t)len + 1;
            }
            /*
             * The entry list above cannot see everything: a backend that
             * blanks its own injected variable in place leaves the block the
             * original LENGTH while contributing no entry. Those bytes still
             * sit in the hashed [stack] VMA and still shift the guest stack,
             * so emit the block verbatim as hex and let the checker compare it
             * byte for byte.
             */
            printf("PROCRAW\t");
            for (ssize_t i = 0; i < total; ++i)
                printf("%02x", (unsigned char)buffer[i]);
            printf("\n");
        }
    }

    printf("SIZES\tenviron_count=%zu environ_bytes=%zu procenv_count=%zu procenv_bytes=%zu\n",
           environ_count, environ_bytes, procenv_count, procenv_bytes);
    return 0;
}
