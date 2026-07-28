#include <signal.h>
#include <stdio.h>
#include <unistd.h>

/*
 * This fixture has no pod or shim knowledge. SIGSTOP is a deterministic test
 * safe point: the parent attaches after exec, injects, and detaches before main
 * resumes. Production injectors must stop every target thread themselves.
 */
int main(void) {
    if (raise(SIGSTOP) != 0) {
        perror("raise(SIGSTOP)");
        return 2;
    }
    puts("ptrace-target-resumed");
    return 0;
}
