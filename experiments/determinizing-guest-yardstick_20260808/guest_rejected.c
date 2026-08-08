/* The REJECTED guest -- deliberately built to fail validation, so the gate is
 * proven to discriminate rather than merely to pass.
 *
 * It looks reasonable: it also "does exactly K things". But its syscall count
 * is a function of the ENVIRONMENT, not of the program:
 *   - printf through stdio: the number of write(2) calls depends on whether
 *     stdout is a tty (line-buffered) or a pipe (block-buffered).
 *   - dynamic libc startup: loader mmaps, locale and tty probes.
 * A guest like this yields a different count per run context, so any backend
 * comparison against it measures the environment and reports it as a backend
 * disagreement. That is the confident-nonsense failure this gate exists to
 * catch BEFORE any backend is compared.
 */
#include <stdio.h>
#define K 10000
int main(void) {
    long acc = 0;
    for (int i = 0; i < K; i++) {
        acc += i;
        if ((i % 1000) == 0) printf("progress %d\n", i);  /* env-dependent writes */
    }
    printf("acc=%ld\n", acc);
    return 0;
}
