DETLOG SCHEDRAND: seeding scheduler runqueue with seed 0
DETLOG USER RAND: seeding PRNG for root thread with seed 0
DETLOG CHAOSRAND: seeding chaos scheduler with seed 0
DETLOG [post_exec, dtid 3] init auxv AT_RANDOM value to [162, 205, 24, 211, 0, 83, 122, 92, 176, 131, 220, 72, 219, 250, 14, 242]
DETLOG [syscall][detcore, dtid 3] inbound syscall: brk(NULL) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #2: brk(NULL) = Ok(4214784)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: arch_prctl(12289, 0x7fffffffec00) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #3: arch_prctl(12289, 0x7fffffffec00) = Err(Errno(EINVAL))
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mmap(NULL, 8192, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_ANON), -1, 0) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #4: mmap(NULL, 8192, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_ANON), -1, 0) = Ok(140737353850880)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: access(0x7ffff7ff2e50 -> "/etc/ld.so.preload", Mode(S_IROTH)) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #5: access(0x7ffff7ff2e50 -> "/etc/ld.so.preload", Mode(S_IROTH)) = Err(Errno(ENOENT))
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: openat(-100, 0x7ffff7ff1266 -> "/etc/ld.so.cache", OFlag(O_CLOEXEC)) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #6: openat(-100, 0x7ffff7ff1266 -> "/etc/ld.so.cache", OFlag(O_CLOEXEC)) = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: fstat(3, 0x7fffffffde30) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #7: fstat(3, 0x7fffffffde30) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mmap(NULL, 28247, ProtFlags(PROT_READ), MapFlags(MAP_PRIVATE), 3, 0) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #8: mmap(NULL, 28247, ProtFlags(PROT_READ), MapFlags(MAP_PRIVATE), 3, 0) = Ok(140737353822208)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: close(3) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #9: close(3) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: openat(-100, 0x7ffff7fba750 -> "/lib64/libc.so.6", OFlag(O_CLOEXEC)) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #10: openat(-100, 0x7ffff7fba750 -> "/lib64/libc.so.6", OFlag(O_CLOEXEC)) = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: read(3, 0x7fffffffdf98, 832) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #11: read(3, 0x7fffffffdf98, 832) = Ok(832)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: pread64(3, 0x7fffffffdb90, 784, 64) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #12: pread64(3, 0x7fffffffdb90, 784, 64) = Ok(784)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: pread64(3, 0x7fffffffdb50, 48, 848) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #13: pread64(3, 0x7fffffffdb50, 48, 848) = Ok(48)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: pread64(3, 0x7fffffffdb00, 68, 896) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #14: pread64(3, 0x7fffffffdb00, 68, 896) = Ok(68)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: fstat(3, 0x7fffffffde30) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #15: fstat(3, 0x7fffffffde30) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: pread64(3, 0x7fffffffda80, 784, 64) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #16: pread64(3, 0x7fffffffda80, 784, 64) = Ok(784)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mmap(NULL, 2138064, ProtFlags(PROT_READ), MapFlags(MAP_PRIVATE | MAP_DENYWRITE), 3, 0) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #17: mmap(NULL, 2138064, ProtFlags(PROT_READ), MapFlags(MAP_PRIVATE | MAP_DENYWRITE), 3, 0) = Ok(140737349943296)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mmap(0x7ffff7c29000, 1527808, ProtFlags(PROT_READ | PROT_EXEC), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_DENYWRITE), 3, 167936) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #18: mmap(0x7ffff7c29000, 1527808, ProtFlags(PROT_READ | PROT_EXEC), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_DENYWRITE), 3, 167936) = Ok(140737350111232)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mmap(0x7ffff7d9e000, 364544, ProtFlags(PROT_READ), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_DENYWRITE), 3, 1695744) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #19: mmap(0x7ffff7d9e000, 364544, ProtFlags(PROT_READ), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_DENYWRITE), 3, 1695744) = Ok(140737351639040)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mmap(0x7ffff7df7000, 24576, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_DENYWRITE), 3, 2056192) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #20: mmap(0x7ffff7df7000, 24576, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_DENYWRITE), 3, 2056192) = Ok(140737352003584)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mmap(0x7ffff7dfd000, 53200, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_ANON), -1, 0) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #21: mmap(0x7ffff7dfd000, 53200, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_FIXED | MAP_ANON), -1, 0) = Ok(140737352028160)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: close(3) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #22: close(3) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mmap(NULL, 12288, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_ANON), -1, 0) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #23: mmap(NULL, 12288, ProtFlags(PROT_READ | PROT_WRITE), MapFlags(MAP_PRIVATE | MAP_ANON), -1, 0) = Ok(140737353809920)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: arch_prctl(ARCH_SET_FS, 140737353811776) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #24: arch_prctl(ARCH_SET_FS, 140737353811776) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: set_tid_address(0x7ffff7fb0a10) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #25: set_tid_address(0x7ffff7fb0a10) = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: set_robust_list(0x7ffff7fb0a20, 24) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #26: set_robust_list(0x7ffff7fb0a20, 24) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: rseq(140737353814240, 32, 0, 1392848979, 0, 0) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #27: rseq(140737353814240, 32, 0, 1392848979, 0, 0) = Err(Errno(ENOSYS))
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mprotect(0x7ffff7df7000, 16384, ProtFlags(PROT_READ)) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #28: mprotect(0x7ffff7df7000, 16384, ProtFlags(PROT_READ)) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mprotect(0x403000, 4096, ProtFlags(PROT_READ)) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #29: mprotect(0x403000, 4096, ProtFlags(PROT_READ)) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: mprotect(0x7ffff7ffb000, 8192, ProtFlags(PROT_READ)) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #30: mprotect(0x7ffff7ffb000, 8192, ProtFlags(PROT_READ)) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: prlimit64(0, 3, NULL, 0x7fffffffe970) = ?
DETLOG prlimit64: pid=0, resource=3, mutation=false, old=8388608:18446744073709551615
DETLOG [syscall][detcore, dtid 3] finish syscall #31: prlimit64(0, 3, NULL, 0x7fffffffe970) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: munmap(0x7ffff7fb3000, 28247) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #32: munmap(0x7ffff7fb3000, 28247) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: getpid() = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #33: getpid() = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: getpid() = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #34: getpid() = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: getpid() = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #35: getpid() = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: getpid() = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #36: getpid() = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: getpid() = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #37: getpid() = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: getpid() = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #38: getpid() = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: getpid() = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #39: getpid() = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: getpid() = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #40: getpid() = Ok(3)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: fstat(1, 0x7fffffffe270) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #41: fstat(1, 0x7fffffffe270) = Ok(0)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: ioctl(1, TCGETS, 0x7fffffffe1d0) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #42: ioctl(1, TCGETS, 0x7fffffffe1d0) = Err(Errno(ENOTTY))
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: getrandom(0x7ffff7e024f8, 8, 1) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #43: getrandom(0x7ffff7e024f8, 8, 1) = Ok(8)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: brk(NULL) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #44: brk(NULL) = Ok(4214784)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: brk(0x426000) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #45: brk(0x426000) = Ok(4349952)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: write(1, 0x4052a0, 15) = ?
DETLOG [syscall][detcore, dtid 3] finish syscall #46: write(1, 0x4052a0, 15) = Ok(15)
DETLOG [memory][dtid 3] 0x7ffffffde000-0x7ffffffff000 MMPermissions(READ | WRITE | PRIVATE) 0 0:0 0 [stack]->H
DETLOG [syscall][detcore, dtid 3] inbound syscall: exit_group(0) = ?
