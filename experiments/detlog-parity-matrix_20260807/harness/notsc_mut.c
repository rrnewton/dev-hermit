/* PLANTED MUTATION of notsc.c: 9 getpid() calls instead of 8. Nothing else changes. */
#include <stdio.h>
#include <stdint.h>
#include <unistd.h>
int main(void) {
    volatile uint64_t stamps[64];
    for (int i = 0; i < 64; ++i) stamps[i] = 0x5a5a5a5a00000000ULL + i;
    for (int i = 0; i < 9; ++i) { (void)getpid(); }   /* 8 -> 9: the plant */
    uint64_t acc = 0;
    for (int i = 0; i < 64; ++i) acc ^= stamps[i];
    printf("tsc_captured=%d\n", acc != 0);
    return 0;
}
