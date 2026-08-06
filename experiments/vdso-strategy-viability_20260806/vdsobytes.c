/* Show the first bytes of each vDSO symbol as the GUEST sees them. */
#define _GNU_SOURCE
#include <elf.h>
#include <stdio.h>
#include <string.h>
#include <sys/auxv.h>
int main(void){
    unsigned char*base=(unsigned char*)getauxval(AT_SYSINFO_EHDR); if(!base){puts("no vdso");return 1;}
    Elf64_Ehdr*eh=(Elf64_Ehdr*)base; Elf64_Phdr*ph=(Elf64_Phdr*)(base+eh->e_phoff);
    Elf64_Dyn*dyn=NULL; long off=0;
    for(int i=0;i<eh->e_phnum;i++){
        if(ph[i].p_type==PT_LOAD) off=(long)base-(long)(ph[i].p_vaddr-ph[i].p_offset);
        if(ph[i].p_type==PT_DYNAMIC) dyn=(Elf64_Dyn*)(base+ph[i].p_offset);
    }
    const char*str=NULL; Elf64_Sym*sym=NULL; long n=0;
    for(Elf64_Dyn*d=dyn; d->d_tag!=DT_NULL; d++){
        if(d->d_tag==DT_STRTAB) str=(const char*)(d->d_un.d_ptr+off);
        if(d->d_tag==DT_SYMTAB) sym=(Elf64_Sym*)(d->d_un.d_ptr+off);
        if(d->d_tag==DT_HASH) n=((Elf32_Word*)(d->d_un.d_ptr+off))[1];
    }
    for(long i=0;i<n;i++){
        const char*nm=str+sym[i].st_name;
        if(strncmp(nm,"__vdso_",7)) continue;
        unsigned char*p=(unsigned char*)(sym[i].st_value+off);
        printf("%-24s", nm);
        for(int k=0;k<8;k++) printf(" %02x", p[k]);
        /* the patch stub is: b8 <sysno32> 0f 05 c3 */
        printf("   %s\n", (p[0]==0xb8 && p[5]==0x0f && p[6]==0x05 && p[7]==0xc3) ? "<- NEUTERED (mov sysno; syscall; ret)" : "");
    }
    return 0;
}
