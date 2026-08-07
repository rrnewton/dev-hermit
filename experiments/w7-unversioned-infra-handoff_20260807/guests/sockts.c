/* PLANT: two SIOCGSTAMP ioctls with NO intervening packet.
   Linux returns the stored last-packet time, byte-identical.
   The claim under test is that hermit advanced it per query. */
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <linux/sockios.h>
int main(void) {
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in a; memset(&a,0,sizeof a);
    a.sin_family=AF_INET; a.sin_addr.s_addr=htonl(INADDR_LOOPBACK); a.sin_port=0;
    if (bind(s,(struct sockaddr*)&a,sizeof a)) { printf("bind_fail\n"); return 1; }
    socklen_t al=sizeof a; getsockname(s,(struct sockaddr*)&a,&al);
    char msg[]="x";
    if (sendto(s,msg,1,0,(struct sockaddr*)&a,sizeof a) < 0) { printf("send_fail\n"); return 1; }
    char buf[8]; if (recv(s,buf,sizeof buf,0) < 0) { printf("recv_fail\n"); return 1; }
    struct timeval t1,t2;
    if (ioctl(s,SIOCGSTAMP,&t1)) { printf("ioctl1_fail\n"); return 1; }
    if (ioctl(s,SIOCGSTAMP,&t2)) { printf("ioctl2_fail\n"); return 1; }
    printf("t1=%ld.%06ld t2=%ld.%06ld same=%d\n",
           (long)t1.tv_sec,(long)t1.tv_usec,(long)t2.tv_sec,(long)t2.tv_usec,
           (t1.tv_sec==t2.tv_sec && t1.tv_usec==t2.tv_usec));
    return 0;
}
