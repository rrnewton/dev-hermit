OUTPUT_FORMAT(elf64-x86-64)
OUTPUT_ARCH(i386:x86-64)
ENTRY(pod_v2_init)

PHDRS
{
  pod PT_LOAD FLAGS(5);
  stack PT_GNU_STACK FLAGS(6);
}

SECTIONS
{
  . = 0;
  .pod : ALIGN(16)
  {
    __pod_start = .;
    KEEP(*(.text.pod_v2_*))
    *(.text .text.*)
    *(.rodata .rodata.* .rodata.cst*)
    __pod_end = .;
  } :pod

  .forbidden :
  {
    *(.data .data.* .bss .bss.* COMMON)
    *(.data.rel.ro .data.rel.ro.*)
    *(.got .got.* .got.plt .plt .plt.*)
    *(.tdata .tdata.* .tbss .tbss.*)
    *(.init .fini .init_array .init_array.* .fini_array .fini_array.*)
    *(.dynamic .dynsym .dynstr .hash .gnu.hash)
  } :pod

  ASSERT(SIZEOF(.forbidden) == 0, "pod contains writable, dynamic, TLS, GOT, or init state")

  /DISCARD/ :
  {
    *(.eh_frame .eh_frame.* .gcc_except_table .gcc_except_table.*)
    *(.comment .note .note.* .llvm_addrsig)
  }
}
