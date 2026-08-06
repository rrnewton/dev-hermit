#include <unistd.h>
extern long raw_getppid(void);
int main(void){
    for (int i=0;i<50;i++) raw_getppid();     /* 50 syscalls from libmylib.so */
    for (int i=0;i<50;i++) getppid();         /* 50 syscalls via libc (allowlisted) */
    write(1,"done\n",5); return 0;
}
