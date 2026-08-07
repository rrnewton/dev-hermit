/* e9patch REACH guest #2: -static -nostdlib, freestanding _start, no PLT.
   Every syscall instruction is in the main ELF by construction. */
static inline long sys3(long n, long a, long b, long c) {
    long r; __asm__ volatile("syscall" : "=a"(r) : "a"(n), "D"(a), "S"(b), "d"(c) : "rcx","r11","memory"); return r;
}
static inline long sys1(long n, long a) {
    long r; __asm__ volatile("syscall" : "=a"(r) : "a"(n), "D"(a) : "rcx","r11","memory"); return r;
}
void _start(void) {
    sys1(39, 0);                              /* getpid  */
    sys1(110, 0);                             /* getppid */
    sys1(102, 0);                             /* getuid  */
    sys3(1, 1, (long)"static_ok\n", 10);      /* write   */
    sys3(1, 1, (long)"static_ok\n", 10);      /* write   (second site) */
    sys1(60, 0);                              /* exit(0) */
    __builtin_unreachable();
}
