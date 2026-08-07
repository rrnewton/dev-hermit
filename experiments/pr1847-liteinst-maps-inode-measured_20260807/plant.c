/* POSITIVE CONTROL for the measurement, not for the fix: reads a host file into
   a live stack buffer, then makes syscalls so the stack is hashed while the
   bytes are resident. The file content is changed between run 1 and run 2, so
   the stack hash MUST differ. If it does not, the harness is inert. */
#include <stdio.h>
#include <unistd.h>
int main(void) {
    volatile char buf[512];
    FILE *f = fopen("/tmp/w2-plant.txt", "r");
    if (!f) { printf("plant_missing\n"); return 1; }
    for (int i = 0; i < 512; ++i) buf[i] = 0;
    if (!fgets((char *)buf, 512, f)) { printf("plant_empty\n"); return 1; }
    fclose(f);
    for (int i = 0; i < 8; ++i) { (void)getpid(); }
    int acc = 0;
    for (int i = 0; i < 512; ++i) acc += buf[i];
    printf("plant_read=%d\n", acc != 0);
    return 0;
}
