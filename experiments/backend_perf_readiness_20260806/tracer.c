#include <stdio.h>
#include <string.h>
int main(void){
    FILE*f=fopen("/proc/self/status","r"); char l[256];
    while(f && fgets(l,sizeof l,f)) if(!strncmp(l,"TracerPid:",10)){ printf("%s",l); break; }
    if(f) fclose(f);
    return 0;
}
