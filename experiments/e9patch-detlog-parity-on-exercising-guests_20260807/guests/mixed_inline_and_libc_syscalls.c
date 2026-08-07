/* e9patch REACH guest #3: inline syscall sites interleaved with ordinary libc
   calls, i.e. a realistic mixed program rather than a pure probe. */
#include <stdio.h>
#include <unistd.h>
#include <stdint.h>
static inline long sys1(long n, long a) {
    long r; __asm__ volatile("syscall" : "=a"(r) : "a"(n), "D"(a) : "rcx","r11","memory"); return r;
}
int main(void) {
    long p = sys1(39, 0);            /* inline getpid -> main-ELF site */
    (void)getpid();                  /* libc getpid  -> libc.so, not rewritable */
    (void)write(1, "mixed_ok\n", 9); /* libc write */
    printf("pid_nonzero=%d\n", p > 0);
    return 0;
}
