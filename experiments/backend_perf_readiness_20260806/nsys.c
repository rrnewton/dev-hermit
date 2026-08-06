#include <unistd.h>
#include <stdlib.h>
int main(int argc, char **argv){
    long n = (argc>1)? atol(argv[1]) : 100;
    for (long i=0;i<n;i++) getppid();   /* one cheap, non-elidable syscall per iter */
    return 0;
}
