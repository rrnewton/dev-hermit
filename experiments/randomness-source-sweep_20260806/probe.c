// Enumerate EVERY randomness source in one process and print each one's bytes.
// One guest, so a single double-run covers the whole set and no source can be
// silently skipped.
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/auxv.h>
#include <sys/syscall.h>
#include <immintrin.h>

static void hex(const char *tag, const unsigned char *b, int n) {
    printf("%-22s ", tag);
    for (int i = 0; i < n; i++) printf("%02x", b[i]);
    printf("\n");
}
static void readfile(const char *tag, const char *path, int n) {
    unsigned char b[64] = {0};
    int fd = open(path, O_RDONLY);
    if (fd < 0) { printf("%-22s <unopenable>\n", tag); return; }
    ssize_t got = read(fd, b, n);
    close(fd);
    if (got <= 0) { printf("%-22s <unreadable>\n", tag); return; }
    hex(tag, b, (int)got);
}
int main(void) {
    unsigned char b[32];

    // 1. getrandom(2), raw syscall (no libc caching in the way)
    memset(b,0,sizeof b);
    if (syscall(SYS_getrandom, b, 16, 0) == 16) hex("getrandom(2)", b, 16);
    else printf("%-22s <failed>\n", "getrandom(2)");

    // 2/3. the random character devices
    readfile("/dev/urandom", "/dev/urandom", 16);
    readfile("/dev/random",  "/dev/random",  16);

    // 4. AT_RANDOM: 16 kernel-supplied bytes in the aux vector (also the seed
    //    for glibc's stack canary and pointer guard)
    {
        unsigned long p = getauxval(AT_RANDOM);
        if (p) hex("AT_RANDOM(auxv)", (const unsigned char *)p, 16);
        else printf("%-22s <absent>\n", "AT_RANDOM(auxv)");
    }

    // 5. RDRAND / RDSEED instructions -- NOT syscalls, so they can only be
    //    controlled by masking the CPUID feature bit or trapping the insn.
    {
        // Probe the CPUID feature bit FIRST. Masking that bit is precisely how a
        // hypervisor/emulator hides RDRAND, so "absent" is itself a finding.
        unsigned int eax=1, ebx=0, ecx=0, edx=0;
        __asm__ __volatile__("cpuid" : "=a"(eax),"=b"(ebx),"=c"(ecx),"=d"(edx) : "a"(1));
        int have_rdrand = (ecx >> 30) & 1;
        if (!have_rdrand) {
            printf("%-22s <CPUID feature bit CLEAR>\n", "RDRAND");
        } else {
            unsigned long long r = 0; unsigned char ok = 0;
            __asm__ __volatile__("rdrand %0; setc %1" : "=r"(r), "=qm"(ok));
            if (ok) printf("%-22s %016llx\n", "RDRAND", r);
            else printf("%-22s <insn returned CF=0>\n", "RDRAND");
        }
    }

    // 6. libc CSPRNG wrappers, which route to getrandom internally
#if defined(__GLIBC__) && (__GLIBC__ > 2 || (__GLIBC__ == 2 && __GLIBC_MINOR__ >= 36))
    printf("%-22s %08x\n", "arc4random", arc4random());
#else
    printf("%-22s <not in this libc>\n", "arc4random");
#endif
    memset(b,0,sizeof b);
    if (getentropy(b, 16) == 0) hex("getentropy(3)", b, 16); else printf("%-22s <failed>\n","getentropy(3)");

    // 7. kernel-generated UUID (a randomness source that is a FILE, not a syscall)
    readfile("/proc/uuid", "/proc/sys/kernel/random/uuid", 36);

    // 8. address-space layout -- a randomness source nobody thinks of as one
    {
        void *heap = malloc(1);
        printf("%-22s stack=%d heap=%d\n", "ASLR(varies?)",
               (int)(((unsigned long)&b >> 12) & 0xffff),
               (int)(((unsigned long)heap >> 12) & 0xffff));
        free(heap);
    }
    return 0;
}
