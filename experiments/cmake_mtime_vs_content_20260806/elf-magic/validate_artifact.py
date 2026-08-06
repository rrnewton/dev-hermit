#!/usr/bin/env python3
"""Is a linkable build artifact structurally complete?

Magic alone is NOT enough: a partial write that preserves the first 4 bytes
keeps valid ELF magic and passes a magic check (proven by the bracket).
ELF is self-describing -- the header declares where the section table ends, so
a truncated file can be detected from its own metadata, with no sidecar hash.
"""
import struct, sys, os

def verdict(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return "MISSING"
    if size == 0:
        return "CORRUPT:empty"
    with open(path, "rb") as fh:
        head = fh.read(64)
    ext = os.path.splitext(path)[1]
    if ext in (".a", ".rlib"):
        if not head.startswith(b"!<arch>\n"):
            return "CORRUPT:bad-archive-magic"
        if size < 68:                      # ar header (8) + one member header (60)
            return "CORRUPT:archive-truncated"
        return "OK"
    # .o .so .lo -> ELF
    if not head.startswith(b"\x7fELF"):
        return "CORRUPT:bad-elf-magic"
    if len(head) < 64:
        return "CORRUPT:elf-header-truncated"
    if head[4] != 2:                        # ELFCLASS64
        return "OK"                         # 32-bit: magic-only check
    e_shoff    = struct.unpack_from("<Q", head, 0x28)[0]
    e_shentsize= struct.unpack_from("<H", head, 0x3A)[0]
    e_shnum    = struct.unpack_from("<H", head, 0x3C)[0]
    need = e_shoff + e_shentsize * e_shnum
    if e_shoff and need > size:
        return f"CORRUPT:truncated(needs {need} bytes, has {size})"
    return "OK"

if __name__ == "__main__":
    bad = 0
    for p in sys.argv[1:]:
        v = verdict(p)
        print(f"  {os.path.basename(p):<26} {v}")
        if v.startswith("CORRUPT"):
            bad += 1
    print(f"checked={len(sys.argv)-1} corrupt={bad}")
    sys.exit(1 if bad else 0)
