# demo-20260804: the tag was already correct — and verifying it is what broke the primaries

**Task:** `cut-demo-tag-20260804-verified` (P1) · **Date:** 2026-08-06 · **Author:** hermit-design
**Host:** devbig014.atn7.facebook.com (316 cores) · **Local only**, egress 403 throughout.

---

## 1. The headline: the tag exists, on the right commit, with evidence

The task asks to cut `demo-20260804`. It was already cut, on 2026-08-04, and it is correct.

```
demo-20260804 -> annotated tag 93b3922b9 -> commit 3e4367ec206c756c9eca5b5427826e30d5a42074
demo-20260731 -> annotated tag 4b2ce2809 -> commit 145efc7d4                (RETAINED)
```

All four ACTION items are already satisfied:

| # | requirement | status |
| --- | --- | --- |
| 1 | on the commit the verified boot RAN AT, not a newer main | ✅ current main is `f89c69766` = `demo-20260804-234-gf89c69766`; the tag was **not** moved to it |
| 2 | cut `demo-20260804` | ✅ annotated |
| 3 | evidence recorded WITH the tag | ✅ cmd, exit 0, serial, wall, budget, regressor context |
| 4 | keep the previous known-good tag | ✅ `demo-20260731` present at `145efc7d` |

Lineage claims in the message check out: `3e4367ec` **is** an ancestor of `origin/main`, and `145efc7d`
(the #1396 merge / prior tag) **is** an ancestor of `3e4367ec`, 62 commits back — matching the log's
`demo-20260731-62-g3e4367ec`.

**I did not re-cut, move, or re-annotate anything.**

The log corroborates the headline numbers: `Exit status: 0`, `Elapsed (wall clock) 2:21.16` (=141.16 s),
serial reached `2022-01-01T00:00:07`, submodules `hermit 3e4367ec…` / `reverie d2fb9a05…`.

---

## 2. Three proxy defects in the recorded evidence

None of these touch the exit-0 / 141 s claim. All are locally checkable.

### Proxy 1 — "(repeat-verified)" is not what that log shows

`demos/05-qemu-boot.py:264-272`:

```python
won_anchor = publish_anchor(run_dir, anchor_dir)
if won_anchor:
    result = "FIRST RUN SAVED"
    print("PASS: this run won the anchor claim; saved first run at …")
else:
    passed, report = compare_runs(anchor, current)   # <-- the ONLY comparison
```

Winning the anchor is precisely the branch where **no comparison happens**. The log prints the
`won_anchor` line and terminates with `=== Demo 5: QEMU Linux Snapshot: FIRST RUN SAVED ===`.

So "won anchor claim" and "Automatic repeat verification PASS" are not two independent facts — they are
the *same* fact, and that fact is the opposite of a repeat. **The tag records ONE boot, described as
repeat-verified.**

### Proxy 2 — the binary was not built from the tagged commit alone

`ignored/qemu-linux/boot-anchor/run-metadata.json`:

```
hermit_version = hermit 0.2.0 (2026-08-04, g3e4367ec206c-dirty)
```

The evidence is keyed to a SHA but was produced by a tree that is `3e4367ec` **plus uncommitted
modifications**. §4 explains *why*, and it is not a one-off.

### Proxy 3 — the named reverie is not the reverie that was linked

| | reverie |
| --- | --- |
| tag message names (parent gitlink at boot time) | `d2fb9a05` |
| **hermit 3e4367ec's `Cargo.lock` actually builds** | **`bbf6e2ef`** |

Verified twice: by reading `git show 3e4367ec:Cargo.lock`, and by building — the build log carries
`rev=bbf6e2eff8bc2070353869bf7244641665b46fd2` ten times.

`bbf6e2ef` **is** an ancestor of `d2fb9a05`, 6 commits behind, and all 6 are reverie-dbi / SaBRe changes
that demo5 (ptrace) does not exercise. Reproduction from the tag is in fact **fine**, because `Cargo.lock`
governs regardless of the gitlink. **This is an attribution error in the record, not a reproduction
defect, and not a reason to remove the tag.**

---

## 3. The reliability claim rests on one sample

I checked whether other evidence covers this commit. It does not.

`experiments/demo5-rootcause-20260731/` — the source of the "18/20 BOOT_OK + load-independence PASSED"
reliability result — is dated **2026-07-31** with `hermit_shas` `f6c836b18dac` / `61d8df393b88`, **both
pre-#1396** (the slow-binary era). It says nothing about `3e4367ec`.

So at the tagged commit, total evidence = **one boot**.

---

## 4. Verifying it is what breaks the primaries — the causal root of Proxy 2

**I ran `python3 demos/05-qemu-boot.py` from the parent and it detached both primary checkouts.**

I had grepped `demos/05-qemu-boot.py` and `demos/lib/demo_common.py` for `submodule`, found nothing, and
concluded it was safe. **That check was wrong**: the demo shells out to `make check-deps`, and that path
runs `git submodule update` on the parent.

| | before | after my run | restored to |
| --- | --- | --- | --- |
| `hermit` | `f89c69766` (main) | **detached** `b4e94ce4` | `f89c69766` main ✅ |
| `reverie` | `025d37800` (main) | **detached** `04a46b43` | `025d37800` main ✅ |
| `hermit/agent-utils` | `a6f4232f` | `089e4b47` | `a6f4232f` ✅ |

Repair was non-destructive. `git checkout main` **refused** in hermit because `README.md` carried a
hand-written prose edit (about the `rrnewton/reverie` fork URL) matching neither `main`, nor `b4e94ce4`,
nor any of main's last 400 commits — live uncommitted work I could not attribute to myself. I did not
discard it: I moved the file aside, switched branch, and restored it **byte-identical**
(sha256 `d878ba7297c33ece…` before and after), with backups at
`ignored/primary-recovery-20260806/`. No `git stash` (shared across worktrees), no force checkout.

**Now the causal point.** The 2026-08-04 verification log shows *the same submodule-checkout lines*. That
run did this too. On a box where a primary is frequently dirty, checking out submodules there produces a
`-dirty` build — which is exactly the `g3e4367ec206c-dirty` in the anchor. **Proxy 2 is not a slip; it is
the predictable output of running demo5 from the parent.**

---

## 5. The measurement: attempted, blocked on host provisioning

I tried to supply the missing evidence — two boots at a clean `3e4367ec`, comparing against each other in
a private `QEMU_ASSETS` dir (never touching the existing anchor, which is another agent's artifact).

**Got:** a clean build. `hermit 0.2.0 (2026-08-06, g3e4367ec206c)` — **no `-dirty`**, unlike the anchor's.
58.96 s, offline, in a throwaway worktree, using the four-variable libunwind incantation
(`PKG_CONFIG_PATH` is the one usually omitted; `LD_LIBRARY_PATH` alone only lets you *run*, not *build*).

**Blocked by:** devbig014 has lost the demo host dependencies since 2026-08-04.

```
rpm -q qemu-kvm-core  -> not installed
rpm -q qemu-img       -> not installed
rpm -q cmake          -> not installed
/usr/bin/qemu*        -> absent      busybox -> absent
```

These were hand-installed on 2026-07-29/30 from backports/epel and are gone. I worked around two of three:

* **QEMU** — `ignored/demo07-drgn_20260728/qemu-root/usr/bin/qemu-system-x86_64` is **byte-identical** to
  the anchor's binary (sha256 `4f45fad875a6e3…`, same `10.1.2-1.4.hs+fb.el9`), so a *valid* comparison was
  genuinely possible; and `qemu-img` sits beside it.
* **busybox** — a static one exists at `ignored/qemu-linux/initramfs-autotest/bin/busybox`.

Both preflights then passed. **cmake** fails `make check-build-tools` (for the DBI backend, which demo5
does not even use). Installing needs sudo + egress; egress is 403. Even with cmake, the recursive
submodule init would need to clone `third-party/e9patch`. Stopped there.

---

## 6. Recommendations (owner decisions; egress + provisioning required)

1. **Re-provision devbig014**: `qemu-system-x86-core`, `qemu-img`, `busybox`, `cmake`. Until then nobody
   can re-verify any demo tag on this box, and the P0 Demo Gate fails preflight in ~0.2 s — a **fast
   preflight FAIL, not a hang**. Do not misread it as the HPET wedge.
2. **Re-annotate `demo-20260804`** to say *single verified boot, 141 s*, dropping "(repeat-verified)" — or
   supply a real second run and keep the clause. I did **not** retag: it is published, and retagging is a
   force-update.
3. **Fix the reverie line** to name the build pin `bbf6e2ef` alongside the gitlink `d2fb9a05`.
4. **Make demo5 refuse to run against a primary/dirty parent**, or run it only from an isolated worktree —
   otherwise it keeps detaching primaries and producing `-dirty` evidence (§4).

---

## 7. Not established

* **No boot was run.** Every claim here is a read of committed state, the 2026-08-04 log, the anchor
  metadata, the demo source, or my own build. **I produced no new demo5 timing or reliability number.**
* **Whether `3e4367ec` actually boots reliably is still open** — one sample, and I could not add to it.
  My findings say the *record* overstates; they do **not** say the boot is unreliable.
* **The `README.md` edit's author is unknown.** Its mtime (`00:19:57`) falls inside my run window, which
  argues it was mine; its content is hand-written prose that no build step would emit, which argues it
  was a concurrent agent's. I could not resolve this, so I treated it as someone else's and preserved it.
* **Remote tag state unverified.** Egress is 403, so `git ls-remote` fails; I confirmed both tags exist
  **locally** only. Memory records `demo-20260804` as pushed to `rrnewton/hermit`, but that is a note, not
  a check.
* **Proxy 3's "6 DBI/SaBRe commits don't affect demo5" is reasoning, not measurement.** I read the six
  subjects; I did not A/B a build against each reverie rev.
* **I did not check whether other demo tags** (`demo-20260729`, etc.) carry the same three defects.
