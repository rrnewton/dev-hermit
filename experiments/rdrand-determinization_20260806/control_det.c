// Positive control: an ordinary deterministic program with NO rdrand/rdseed.
#include <stdio.h>
#include <sys/random.h>
int main(void) {
    unsigned long long g = 0;
    getrandom(&g, sizeof g, 0);
    printf("getrandom %016llx\n", g);
    long acc = 0; for (long i = 0; i < 100000; i++) acc += i % 7;
    printf("acc %ld\n", acc);
    return 0;
}
