#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(void){
    for (int i=0;i<8;i++){ char *p = malloc(1<<16); memset(p,'A'+i,1<<16); if(i%2) free(p); }
    char *big = malloc(4<<20); memset(big,'Z',4<<20);   /* force brk/mmap growth */
    write(1,"heap\n",5); return 0;
}
