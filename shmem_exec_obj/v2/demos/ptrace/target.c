#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

/*
 * This fixture has no pod or shim knowledge. Two inherited pipes provide a
 * deterministic test safe point: the parent attaches after exec, injects, and
 * detaches before releasing the read below. Production injectors must discover
 * a suitable safe point and stop every target thread themselves.
 */
static int environment_fd(const char *name) {
    const char *value = getenv(name);
    char *end = NULL;
    long parsed;

    if (value == NULL || *value == '\0') {
        fprintf(stderr, "%s is missing\n", name);
        return -1;
    }
    errno = 0;
    parsed = strtol(value, &end, 10);
    if (errno != 0 || *end != '\0' || parsed <= STDERR_FILENO ||
        parsed > INT_MAX) {
        fprintf(stderr, "%s is invalid\n", name);
        return -1;
    }
    return (int)parsed;
}

int main(void) {
    int ready_fd = environment_fd("INJECTION_FIXTURE_READY_FD");
    int resume_fd = environment_fd("INJECTION_FIXTURE_RESUME_FD");
    char byte = 'R';

    if (ready_fd < 0 || resume_fd < 0 || ready_fd == resume_fd) {
        return 2;
    }
    if (write(ready_fd, &byte, 1) != 1) {
        perror("write fixture readiness");
        return 2;
    }
    close(ready_fd);
    do {
        errno = 0;
        if (read(resume_fd, &byte, 1) == 1) {
            break;
        }
    } while (errno == EINTR);
    close(resume_fd);
    if (byte != 'G') {
        fputs("invalid fixture resume message\n", stderr);
        return 2;
    }
    puts("ptrace-target-resumed");
    return 0;
}
