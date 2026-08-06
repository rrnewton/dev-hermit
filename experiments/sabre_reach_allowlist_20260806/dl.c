#include <dlfcn.h>
#include <unistd.h>
int main(void){
    write(1,"pre\n",4);
    void *h = dlopen("libm.so.6", RTLD_NOW);   /* ld.so does openat/read/mmap/mprotect HERE, post-startup */
    write(1, h ? "dlopen-ok\n" : "dlopen-fail\n", h?10:12);
    if(h) dlclose(h);
    return 0;
}
