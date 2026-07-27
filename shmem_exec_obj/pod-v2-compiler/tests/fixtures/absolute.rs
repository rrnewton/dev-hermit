#![no_std]

core::arch::global_asm!(
    r#"
    .section .text.pod_v2_init,"ax",@progbits
    .globl pod_v2_init
    .type pod_v2_init,@function
pod_v2_init:
    ret
    .size pod_v2_init, .-pod_v2_init

    .section .text.pod_v2_absolute,"ax",@progbits
    .globl pod_v2_absolute
    .type pod_v2_absolute,@function
pod_v2_absolute:
    .quad .text.pod_v2_init
    ret
    .size pod_v2_absolute, .-pod_v2_absolute
"#
);
