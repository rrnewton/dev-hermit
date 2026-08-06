# Curated evidence excerpts (hermit 0.2.0 gf89c69766371, ptrace, --strict)

## Mechanism: batch-local sort. desc200 (host order descending), buffer 1024.
Each getdents64 result buffer is internally ascending, but the buffers
themselves arrive in host order, so the global stream descends by block.
```
-- batch 0 nbytes=1008 nents=32   first-entry=.
-- batch 1 nbytes=1024 nents=32   first-entry=n0138
-- batch 2 nbytes=1024 nents=32   first-entry=n0106
-- batch 3 nbytes=1024 nents=32   first-entry=n0074
-- batch 4 nbytes=1024 nents=32   first-entry=n0042
-- batch 5 nbytes=1024 nents=32   first-entry=n0010
-- batch 6 nbytes=320 nents=10   first-entry=n0000
```

## Same content, opposite host order => different guest-visible order
```
hermit asc200  b1024 head: . .. n0000 n0001 n0002 n0003 
hermit desc200 b1024 head: . .. n0170 n0171 n0172 n0173 
hermit asc200  b65536 head: . .. n0000 n0001 n0002 n0003 
hermit desc200 b65536 head: . .. n0000 n0001 n0002 n0003 
```

## Cross-filesystem: identical content and creation order, btrfs vs tmpfs, buffer 1024
```
hermit btrfs/desc200 head: . .. n0170 n0171 n0172 n0173 
hermit tmpfs/desc200 head: . .. n0000 n0001 n0002 n0003 
```

## d_off cookie monotonicity (Linux guarantees no inversions)
```
native asc200    b65536 : d_off_monotonic entries=202 inversions=0 verdict=PASS
hermit asc200    b65536 : d_off_monotonic entries=202 inversions=0 verdict=PASS
native asc200    b1024  : d_off_monotonic entries=202 inversions=0 verdict=PASS
hermit asc200    b1024  : d_off_monotonic entries=202 inversions=0 verdict=PASS
native desc200   b65536 : d_off_monotonic entries=202 inversions=0 verdict=PASS
hermit desc200   b65536 : d_off_monotonic entries=202 inversions=199 verdict=FAIL
native desc200   b1024  : d_off_monotonic entries=202 inversions=0 verdict=PASS
hermit desc200   b1024  : d_off_monotonic entries=202 inversions=193 verdict=FAIL
native desc2000  b65536 : d_off_monotonic entries=2002 inversions=0 verdict=PASS
hermit desc2000  b65536 : d_off_monotonic entries=2002 inversions=1999 verdict=FAIL
native desc2000  b1024  : d_off_monotonic entries=2002 inversions=0 verdict=PASS
hermit desc2000  b1024  : d_off_monotonic entries=2002 inversions=1937 verdict=FAIL
```

## telldir/seekdir round-trip (first 64 entries)
```
native asc200   : seekdir_roundtrip entries=64 mismatches=0 verdict=PASS
hermit asc200   : seekdir_roundtrip entries=64 mismatches=0 verdict=PASS
native desc200  : seekdir_roundtrip entries=64 mismatches=0 verdict=PASS
hermit desc200  : seekdir_roundtrip entries=64 mismatches=61 verdict=FAIL
native desc2000 : seekdir_roundtrip entries=64 mismatches=0 verdict=PASS
hermit desc2000 : seekdir_roundtrip entries=64 mismatches=62 verdict=FAIL
```

## --verify-strict is BASELINE-RED on this host (control included)
```
/bin/true rc=1  bitwise_parity=False
Mismatch at log messages 5 (run 1) and 5 (run 2)
Mismatch at log messages 6 (run 1) and 6 (run 2)
reverie_ptrace::vdso: 3 patched __vdso_time@7f60e2feda
```
