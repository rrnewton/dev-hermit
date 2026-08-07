/* e9patch REACH guest #1: several DISTINCT inline `syscall` instructions in the
   MAIN ELF. A dynamically-linked guest normally issues syscalls from libc.so, so
   the main ELF holds nothing for e9patch to rewrite; these do.
   stdout is a fixed string so the guest cannot itself be a source of divergence. */
#include <stdint.h>
static inline long sys1(long n, long a) {
    long r; __asm__ volatile("syscall" : "=a"(r) : "a"(n), "D"(a) : "rcx","r11","memory"); return r;
}
static inline long sys3(long n, long a, long b, long c) {
    long r; __asm__ volatile("syscall" : "=a"(r) : "a"(n), "D"(a), "S"(b), "d"(c) : "rcx","r11","memory"); return r;
}
int main(void) {
    long acc = 0;
    acc += sys1(39, 0);              /* getpid   */
    acc += sys1(39, 0);              /* getpid   (second distinct site) */
    acc += sys1(110, 0);             /* getppid  */
    acc += sys1(102, 0);             /* getuid   */
    sys3(1, 1, (long)"inline_ok\n", 10);   /* write(1, ...) */
    return acc > 0 ? 0 : 1;
}
