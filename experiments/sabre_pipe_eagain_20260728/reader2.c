#define _GNU_SOURCE
#include <unistd.h>
#include <stdio.h>
#include <fcntl.h>
#include <string.h>
int main(void){
    FILE*m=fopen("/proc/self/maps","r"); char line[512]; int plugin=0,sabre=0;
    while(m&&fgets(line,sizeof line,m)){ if(strstr(line,"libdetcore_sabre"))plugin=1; if(strstr(line,"/sabre"))sabre=1; }
    if(m)fclose(m);
    fprintf(stderr,"CHILD plugin_loaded=%d sabre_loader=%d\n",plugin,sabre);
    int fl=fcntl(0,F_GETFL); fprintf(stderr,"STDIN O_NONBLOCK=%d\n",(fl&O_NONBLOCK)?1:0);
    char b[64]; ssize_t n=read(0,b,sizeof b); fprintf(stderr,"READ n=%zd\n",n); return n>0?0:1;
}
