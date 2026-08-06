#include <unistd.h>
#include <fcntl.h>
int main(void){
    int fd = open("/etc/hostname", O_RDONLY);
    char b[64]; if (fd>=0){ read(fd,b,sizeof b); close(fd);} 
    write(1, "hi\n", 3);
    return 0;
}
