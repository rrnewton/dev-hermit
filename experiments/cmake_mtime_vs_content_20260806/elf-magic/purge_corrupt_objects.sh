#!/usr/bin/env bash
# Replacement for validate.sh purge_zero_byte_objects.
# Binds to the artifact's OWN IDENTITY (format magic) instead of the `size==0`
# proxy, and covers every linkable output -- not just *.o.
#
# CRITICAL: magic is PER-FORMAT. .a/.rlib are ar archives ("!<arch>\n"), NOT ELF.
# A naive "validate ELF magic" over the widened set deletes every valid archive.
artifact_is_corrupt() {
    local f=$1 magic
    [[ -s $f ]] || return 0                      # empty => corrupt (old behaviour)
    magic=$(head -c 8 -- "$f" 2>/dev/null | od -An -tx1 | tr -d ' \n')
    case "$f" in
        *.a|*.rlib) [[ $magic == 213c617263683e0a* ]] && return 1 || return 0 ;;  # !<arch>\n
        *.o|*.so|*.so.*|*.lo) [[ $magic == 7f454c46* ]] && return 1 || return 0 ;; # \x7fELF
        *) return 1 ;;
    esac
}
purge_corrupt_objects() {
    local root=$1 removed=0 f
    [[ -d $root ]] || { printf 0; return 0; }
    while IFS= read -r -d '' f; do
        artifact_is_corrupt "$f" && rm -f -- "$f" && removed=$((removed + 1))
    done < <(find "$root" -type f \( -name '*.o' -o -name '*.a' -o -name '*.so' \
                -o -name '*.so.*' -o -name '*.rlib' -o -name '*.lo' \) -print0 2>/dev/null)
    printf '%s' "$removed"
}
