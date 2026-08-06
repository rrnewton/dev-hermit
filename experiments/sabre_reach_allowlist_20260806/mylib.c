#include <stddef.h>
long raw_getppid(void){          /* raw syscall instruction INSIDE a non-allowlisted .so */
    long r; __asm__ volatile("syscall" : "=a"(r) : "a"(110) : "rcx","r11","memory");
    return r;
}
