#![no_std]

core::arch::global_asm!(
    r#"
    .section .text.pod_v2_init,"ax",@progbits
    .globl pod_v2_init
    .type pod_v2_init,@function
pod_v2_init:
    ret
    .size pod_v2_init, .-pod_v2_init

    .section .text.pod_v2_outside_addend,"ax",@progbits
    .globl pod_v2_outside_addend
    .type pod_v2_outside_addend,@function
pod_v2_outside_addend:
    .long .text.pod_v2_init + 0x100000 - .
    ret
    .size pod_v2_outside_addend, .-pod_v2_outside_addend
"#
);
