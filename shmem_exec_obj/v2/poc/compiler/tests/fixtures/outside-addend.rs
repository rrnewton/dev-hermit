#![no_std]

core::arch::global_asm!(
    r#"
    .section .text.shmem_pod_init,"ax",@progbits
    .globl shmem_pod_init
    .type shmem_pod_init,@function
shmem_pod_init:
    ret
    .size shmem_pod_init, .-shmem_pod_init

    .section .text.shmem_pod_outside_addend,"ax",@progbits
    .globl shmem_pod_outside_addend
    .type shmem_pod_outside_addend,@function
shmem_pod_outside_addend:
    .long .text.shmem_pod_init + 0x100000 - .
    ret
    .size shmem_pod_outside_addend, .-shmem_pod_outside_addend
"#
);
