#![no_std]

core::arch::global_asm!(
    r#"
    .section .text.shmem_pod_init,"ax",@progbits
    .globl shmem_pod_init
    .type shmem_pod_init,@function
shmem_pod_init:
    ret
    .size shmem_pod_init, .-shmem_pod_init

    .section .text.shmem_pod_absolute,"ax",@progbits
    .globl shmem_pod_absolute
    .type shmem_pod_absolute,@function
shmem_pod_absolute:
    .quad .text.shmem_pod_init
    ret
    .size shmem_pod_absolute, .-shmem_pod_absolute
"#
);
