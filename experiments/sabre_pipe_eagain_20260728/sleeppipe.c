#define _GNU_SOURCE
#include <unistd.h>
#include <stdio.h>
#include <time.h>
#include <sys/wait.h>
int main(void){
    int fd[2]; if(pipe(fd)) return 2;
    pid_t p=fork();
    if(p==0){ close(fd[0]);
        struct timespec ts={0,50*1000*1000}; nanosleep(&ts,0); /* writer sleeps 50ms */
        write(fd[1],"hello",5); close(fd[1]); _exit(0); }
    close(fd[1]);
    char buf[16]; ssize_t n=read(fd[0],buf,sizeof buf); /* blocking read; writer not ready */
    printf("READ n=%zd\n", n);
    int st; waitpid(p,&st,0);
    return n==5?0:1;
}
