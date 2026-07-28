#define _GNU_SOURCE
#include <unistd.h>
#include <stdio.h>
#include <fcntl.h>
int main(void){
    int fl = fcntl(0, F_GETFL);
    fprintf(stderr,"STDIN O_NONBLOCK=%d\n", (fl & O_NONBLOCK)?1:0);
    char buf[64]; ssize_t n = read(0, buf, sizeof buf);
    fprintf(stderr,"READ n=%zd errno-ish\n", n);
    return n>0?0:1;
}
