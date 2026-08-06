// Premise probe: does a guest SIGILL (UD2) reach Detcore's signal handler?
#include <stdio.h>
int main(void) {
    printf("before ud2\n");
    fflush(stdout);
    __asm__ volatile("ud2");
    printf("after ud2\n");
    return 0;
}
