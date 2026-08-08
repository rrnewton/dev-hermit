/* Guest #1 of the "known invariant by construction" family.
 *
 * INVARIANT: this program executes EXACTLY K getpid syscalls, then exactly one
 * exit_group. Total = K + 1. Nothing else, ever, in any environment.
 *
 * WHY FREESTANDING. The invariant is destroyed by libc, not by the loop. A
 * dynamically linked C program emits a variable prologue -- loader mmaps,
 * locale probes, stdio buffer setup, malloc arena init -- and those vary with
 * tty-vs-pipe, environment size, and ASLR. The measured DBT-vs-ptrace delta
 * recorded in reverie/experimental/dbt-strace-vs-ptrace.md was exactly this:
 * a glibc prlimit64 probe on one side, an ioctl probe on the other, neither
 * a tool defect. So: -nostdlib -static, our own _start, no libc at all.
 *
 * WHY getpid. It cannot block, cannot be interrupted (so no EINTR restart
 * adding a syscall under load), takes no arguments, has no side effects, and
 * returns the same value on every call within a process -- which makes the
 * result self-checkable without an external observer.
 *
 * SELF-CHECKING. Every available external syscall counter on this host is
 * ptrace-based, and ptrace is itself a backend we intend to test. A guest that
 * can only be validated through a backend is not a golden reference. So the
 * guest validates itself: it compares all K returns and exits 0 only if the
 * loop completed K times with a consistent result. Exit code is the invariant's
 * observable consequence, and it is readable natively and under every backend
 * with the same one-line check.
 */

#define K 10000

static long sys_getpid(void) {
    long ret;
    __asm__ volatile("syscall" : "=a"(ret) : "a"(39) : "rcx", "r11", "memory");
    return ret;
}

static __attribute__((noreturn)) void sys_exit_group(int code) {
    __asm__ volatile("syscall" :: "a"(231), "D"((long)code) : "rcx", "r11", "memory");
    __builtin_unreachable();
}

void _start(void) {
    long first = sys_getpid();          /* syscall 1 */
    long count = 1;
    for (long i = 1; i < K; i++) {      /* syscalls 2..K */
        if (sys_getpid() != first) {
            sys_exit_group(2);          /* inconsistent pid: reject */
        }
        count++;
    }
    if (count != K) {
        sys_exit_group(3);              /* loop did not run K times: reject */
    }
    sys_exit_group(0);                  /* syscall K+1 */
}
