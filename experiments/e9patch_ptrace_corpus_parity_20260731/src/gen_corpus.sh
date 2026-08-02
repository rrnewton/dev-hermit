#!/bin/bash
# Generate freestanding (-nostdlib -static) raw-syscall guests for e9patch
# preprocessing corpus. Freestanding is required: this host has no static libc.
# Every guest ends in exit_group (231); a bare exit(60) would hang single-thread.
set -euo pipefail
OUT="${1:?usage: gen_corpus.sh <srcdir>}"
mkdir -p "$OUT"
hdr='static long sc(long n,long a,long b,long c,long d,long e,long f){long r;register long r10 __asm__("r10")=d;register long r8 __asm__("r8")=e;register long r9 __asm__("r9")=f;__asm__ volatile("syscall":"=a"(r):"a"(n),"D"(a),"S"(b),"d"(c),"r"(r10),"r"(r8),"r"(r9):"rcx","r11","memory");return r;}
__attribute__((noreturn)) static void die(int s){sc(231,s,0,0,0,0,0);__builtin_unreachable();}'

# 1 minimal_exit: single site (exit only)
cat > "$OUT/minimal_exit.c" <<EOF
$hdr
void _start(void){ die(0); }
EOF
# 2 write_stdout
cat > "$OUT/write_stdout.c" <<EOF
$hdr
void _start(void){ const char m[]="corpus-write\n"; sc(1,1,(long)m,13,0,0,0); die(0); }
EOF
# 3 getpid_check (pid virtualized; just exercise + exit 0)
cat > "$OUT/getpid_check.c" <<EOF
$hdr
void _start(void){ long p=sc(39,0,0,0,0,0,0); die(p>0?0:1); }
EOF
# 4 clock_gettime MONOTONIC
cat > "$OUT/clock_gettime.c" <<EOF
$hdr
void _start(void){ long ts[2]={0,0}; long r=sc(228,1,(long)ts,0,0,0,0); die(r==0?0:1); }
EOF
# 5 nanosleep
cat > "$OUT/nanosleep.c" <<EOF
$hdr
void _start(void){ long ts[2]={0,1000000}; long r=sc(35,(long)ts,0,0,0,0,0); die(r==0?0:1); }
EOF
# 6 getrandom (determinized stream)
cat > "$OUT/getrandom.c" <<EOF
$hdr
void _start(void){ char b[16]; long r=sc(318,(long)b,16,0,0,0,0); die(r==16?0:1); }
EOF
# 7 multi_site: three distinct noinline syscall sites
cat > "$OUT/multi_site.c" <<EOF
static long __attribute__((noinline)) s_write(const char*m,long n){long r;__asm__ volatile("syscall":"=a"(r):"a"(1L),"D"(1L),"S"(m),"d"(n):"rcx","r11","memory");return r;}
static long __attribute__((noinline)) s_getpid(void){long r;__asm__ volatile("syscall":"=a"(r):"a"(39L):"rcx","r11","memory");return r;}
static void __attribute__((noinline,noreturn)) s_exit(long c){__asm__ volatile("syscall"::"a"(231L),"D"(c):"rcx","r11","memory");__builtin_unreachable();}
void _start(void){ const char m[]="multi\n"; s_write(m,6); (void)s_getpid(); s_exit(0); }
EOF
# 8 loop_write: same site 8x
cat > "$OUT/loop_write.c" <<EOF
$hdr
void _start(void){ const char m[]="x"; for(int i=0;i<8;i++) sc(1,1,(long)m,1,0,0,0); sc(1,1,(long)"\n",1,0,0,0); die(0); }
EOF
# 9 mmap_anon: map, touch, unmap
cat > "$OUT/mmap_anon.c" <<EOF
$hdr
void _start(void){ long p=sc(9,0,4096,3,0x22,-1,0); if(p<0) die(1); volatile char*b=(char*)p; b[0]=7; long r=sc(11,p,4096,0,0,0,0); die(r==0?0:1); }
EOF
# 10 uname
cat > "$OUT/uname.c" <<EOF
$hdr
void _start(void){ char buf[390]; long r=sc(63,(long)buf,0,0,0,0,0); die(r==0?0:1); }
EOF
# 11 gettid + rt_sigprocmask
cat > "$OUT/sigmask.c" <<EOF
$hdr
void _start(void){ long t=sc(186,0,0,0,0,0,0); long set=0; long r=sc(14,0,(long)&set,0,8,0,0); die((t>0&&r==0)?0:1); }
EOF
# 12 compute_then_exit: CPU work (RCB preemption) then exit
cat > "$OUT/compute.c" <<EOF
$hdr
void _start(void){ volatile long acc=0; for(long i=0;i<200000;i++) acc+=i%7; die((int)(acc&1)); }
EOF
echo "generated $(ls "$OUT"/*.c | wc -l) guests"
