# Btrfs subvolume deletion inodes-xarray race

Date: 2026-07-28

## Scope and source state

This is the understanding phase for the concurrency-bug reproducer. No kernel
code was changed and the upstream reproducer was not run.

The standalone, ignored checkout is at `ignored/linux` (it is not a submodule).
Its configured remotes are:

```text
origin   git@github.com:rrnewton/linux.git
fb       https://git.internal.tfbnw.net/repos/git/rw/kernel/linux.git
tj       https://git.kernel.org/pub/scm/linux/kernel/git/tj/sched_ext.git
upstream git@github.com:torvalds/linux.git
```

The two study branches identify the relevant states:

```text
research/btrfs-subvol-buggy 9786531399a679fc2f4630d2c0a186205282ab2f
research/btrfs-subvol-fix   f6a6c280059c4ddc23e12e3de1b01098e240036f
```

The fix is `f6a6c280059c (btrfs: don't delete a new inode from the inodes
xarray when deleting an old inode)`. It fixes the xarray conversion introduced
by `310b2f5d5a9451b708ab1d3385c3b0998084904c`.

## The broken invariant

`btrfs_root::inodes` maps an inode number to the current `btrfs_inode` object
for that root. The buggy destructor treated the integer inode number as proof
of object ownership:

```c
entry = __xa_erase(&root->inodes, btrfs_ino(inode));
if (entry == inode)
        empty = xa_empty(&root->inodes);
```

That is unsafe because VFS permits an old inode object and its replacement to
overlap during destruction. The key is unchanged while the object identity is
not.

## Triggering interleaving

Let A be the old inode object and B a new object for the same Btrfs root and
inode number.

1. VFS begins evicting A and calls `btrfs_evict_inode()`.
2. `evict()` removes A from the global VFS inode hash with
   `remove_inode_hash()` in `fs/inode.c`, but has not yet reached
   `destroy_inode(A)` and Btrfs's `btrfs_destroy_inode()`.
3. A concurrent lookup calls `btrfs_iget_path()` and
   `btrfs_iget_locked()`. Because A is no longer in the VFS inode hash,
   `iget5_locked_rcu()` allocates B for the same inode number.
4. `btrfs_read_locked_inode()` adds B to `root->inodes`. Its xarray store
   replaces the still-present pointer to A with B.
5. A finally reaches `btrfs_del_inode_from_root(A)`. In the buggy parent,
   `__xa_erase(..., ino)` erases the slot by index, so it removes B. The later
   `entry == inode` test is too late to restore B; it only prevents this call
   from declaring the xarray empty.

This is an ABA-like stale-owner bug: A deletes an xarray entry that has been
reused by B. The old rbtree implementation did not have this property because
each inode object owned its embedded `rb_node`; removing A's node could not
unlink B's node. The xarray conversion made the slot shared by key and needed
an identity check that the initial conversion omitted.

## Why subvolume deletion can spin

The lost B remains a live VFS inode but is no longer visible in
`root->inodes`. During deletion, once the remaining tracked inode is evicted,
the xarray can appear empty and the root can be queued as a dead root even
though B is still alive.

If B owns a `btrfs_delayed_node`, its inode reference keeps the delayed node's
refcount above zero. `__btrfs_release_delayed_node()` removes an entry from
`root->delayed_nodes` only when that refcount reaches zero. Snapshot cleanup
calls `btrfs_kill_all_delayed_nodes()`, but killing the node's work and dropping
the cleanup iterator reference cannot drop B's inode-owned reference. The
delayed-node xarray therefore never empties, so cleanup repeatedly finds the
same node and can soft-lock the CPU. Memory pressure can mask the problem by
eventually evicting B and releasing its final delayed-node reference.

## Fix semantics

The fix changes the erase into an identity-checked compare/exchange while
holding the xarray lock:

```c
entry = __xa_cmpxchg(&root->inodes, btrfs_ino(inode), inode, NULL,
                     GFP_ATOMIC);
```

The slot is cleared only if it still contains the exact inode object being
destroyed. If B has replaced A, the operation returns B and leaves it present.
Consequently the empty-xarray/dead-root transition is evaluated only after a
successful removal of A. The operation replaces an existing pointer with
`NULL`, so it does not require allocation; `GFP_ATOMIC` also makes the locking
constraint explicit.

## Upstream reproducer shape

The original patch includes a shell reproducer based on a `for-next` kernel
with `CONFIG_PREEMPT_NONE`. It repeatedly creates a Btrfs subvolume and races
cache dropping against `stat` loops over a file and four hard links. An
`inotifywatch` reference holds the target inode without dentries, while an open
dummy file holds the last other tracked inode. After deleting the subvolume,
closing the dummy fd makes `root->inodes` look empty even though the lost inode
remains live. Remounting wakes the cleaner; an affected kernel loops in delayed
node cleanup.

A future repro milestone needs an isolated VM, a disposable Btrfs block
device, root privileges, `btrfs-progs`, `inotify-tools`, and a kernel built with
the target preemption configuration. It should compare the parent and fixed
commits without running against a host filesystem.

## Discussion and corroboration

Public thread:

- Original patch and full reproducer (canonical archive URL):
  https://lore.kernel.org/linux-btrfs/f7e05205fd33d9e510ec1295e0cc8cfdf395cb89.1756237895.git.osandov@osandov.com/
- Public mirror of the original patch:
  https://www.spinics.net/lists/linux-btrfs/msg157605.html
- Josef Bacik review and xarray-conversion context:
  https://www.spinics.net/lists/linux-btrfs/msg157606.html
- Omar Sandoval follow-up on the surrounding lock:
  https://www.spinics.net/lists/linux-btrfs/msg157607.html
- Filipe Manana review and reproducer confirmation:
  https://www.spinics.net/lists/linux-btrfs/msg157614.html
- David Sterba's question about the `Fixes` target and xarray locking:
  https://www.spinics.net/lists/linux-btrfs/msg157693.html
- Omar's explanation of the old embedded-rbtree-node behavior and
  `__xa_cmpxchg` locking:
  https://www.spinics.net/lists/linux-btrfs/msg157704.html
- David's acknowledgement:
  https://www.spinics.net/lists/linux-btrfs/msg157708.html
- Stable commit announcement:
  https://www.spinics.net/lists/stable-commits/msg427547.html
- CVE-2025-39884 summary:
  https://ubuntu.com/security/CVE-2025-39884

Internal context:

- Kernel Status Report 5/19/25-5/23/25 describes the fleet soft lockup in
  subvolume-deletion cleanup and the initial refcount investigation:
  https://fb.workplace.com/groups/kernel.status/permalink/28565816629706914/
- Kernel Status Report 05/19/2025-05/30/2025 records continued BPF tracing of
  delayed-node refcounts and the related cleanup series:
  https://fb.workplace.com/groups/kernel.status/permalink/28613466301608613/
- A later CrashCortex investigation explicitly distinguishes this fixed lost
  inode/infinite-loop race from a separate `root->inodes` spinlock contention
  failure:
  https://fb.workplace.com/groups/4508446156040829/permalink/4515996088619169/

The public review converged on `310b2f5d5a94` as the correct introducing
commit: the prior outer inode lock did not synchronize the VFS inode hash, and
the old rbtree was protected by per-object node identity rather than that lock.
