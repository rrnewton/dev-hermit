#include <stdio.h>
#include <unistd.h>
int main(void){ printf("pid=%d ppid=%d\n",(int)getpid(),(int)getppid()); return 0; }
