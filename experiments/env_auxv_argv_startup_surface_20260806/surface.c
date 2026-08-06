#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <string.h>
extern char **environ;
int main(int argc, char **argv){
    /* argv: contents + order */
    for (int i=0;i<argc;i++) printf("ARGV[%d]=%s\n", i, argv[i]);
    /* environ: contents AND order -- printed in situ, not sorted */
    for (int i=0; environ[i]; i++) printf("ENV[%d]=%s\n", i, environ[i]);
    /* auxv: every type/value pair */
    int fd = open("/proc/self/auxv", O_RDONLY);
    if (fd >= 0){
        unsigned long p[2];
        while (read(fd,p,sizeof p)==(ssize_t)sizeof p && p[0])
            printf("AUXV type=%lu value=0x%lx\n", p[0], p[1]);
        close(fd);
    } else printf("AUXV unavailable\n");
    /* placement: relative offsets only (absolute addrs are the layout task's axis) */
    printf("PLACE argv0_minus_envp0=%ld\n", (long)((char*)argv[0] - (char*)environ[0]));
    return 0;
}
