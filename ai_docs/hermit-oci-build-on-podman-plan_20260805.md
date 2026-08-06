# Hermit OCI integration, second plan: build **on podman** instead of reimplementing OCI

- **Task:** `research-oci-integration-next-phases` (P2 research, owner-requested "second plan").
- **Status:** research complete; **not pushed / not linked** — box-wide egress 403 at time of writing (2026-08-05). All work below is local.
- **Author:** impl agent, claude-opus-5.
- **Host:** `devbig014.atn7.facebook.com`, CentOS Stream 9, kernel `6.18.39-0_fbk0_hardened_0_ga43d5727b443`, 316 CPUs, btrfs.
- **Tooling measured:** podman `5.8.3` (rootless), buildah `1.43.2`, crun `1.28`, conmon `2.2.1`, netavark `2.0.0`. **No** docker, **no** skopeo, **no** runc on this box.
- **Hermit measured:** `hermit 0.2.0 (2026-08-04, g0f891e432a75-dirty)` — the primary checkout's `target/debug/hermit`. Read-only use of the primary; no primary mutation.

Everything numeric in this document was measured on this box during this task. Nothing is
extrapolated from documentation. Section 10 gives the exact reproduction commands.

---

## 0. Headline

**The framing "podman *versus* Rust OCI libs" does not describe the actual choice, because the
shipped plan already runs on the podman stack.** The landed `hermit run --image` shells out to
`buildah`, and buildah's store *is* podman's store:

```
podman info  -> store.graphRoot = /home/newton/.local/share/containers/storage   (5 images)
buildah info -> store.GraphRoot = /home/newton/.local/share/containers/storage   (5 images)
```

Verified end-to-end with **no registry and no egress**, using a bare local podman image ID:

```
$ hermit run --image 097d2bc97c7d -- /bin/cat /etc/os-release
PRETTY_NAME="Ubuntu 24.04.4 LTS"        # host is CentOS Stream
```

So the owner's "discover and run already-pulled podman images" compromise (question 5) is **already
working today** for the podman half. What is actually open is a set of four *independently
selectable* integration surfaces, and a separate question about hermeticizing image *builds*.

The recommendation, in one line: **keep shelling out (never link the Go libraries), move
materialization from copy-out to overlay-in-place, add `hermit oci` as a discovery/UX layer, and add
a `hermit-as-oci-runtime` shim only when `podman ps` visibility is actually wanted — because the
capability argument that motivated avoiding containers turns out to be false.**

Two premises in the task prompt were **refuted by measurement** (§4, §6). Nine concrete defects and
gaps were found in the landed code and in the environment (§8).

---

## 1. Names used in this document

Per the workspace naming rule, each option gets a stable descriptive slug, not an ordinal.

| Slug | What it is | Status today |
| --- | --- | --- |
| `oci-rootfs-materialize` | Landed `hermit run --image`: `buildah from`/`mount`, `cp -a` the merged tree into a hermit-owned cache dir, `chroot`. | **Landed** (hermit `74319f9bb`, `c68f150aa`, `6e24a159c`) |
| `podman-store-overlay` | Read the image's ordered overlay dirs from podman, then overlay-mount them **in place** inside hermit's own user namespace. No copy. | Mechanism verified, not implemented |
| `hermit-as-oci-runtime` | Install hermit (or a shim) as podman's `--runtime`; podman builds the bundle, hermit runs it. Container appears in `podman ps`. | Interception verified, not implemented |
| `libpod-rest-client` | Talk to the libpod REST API over the rootless unix socket instead of forking the CLI. | Endpoint verified, not implemented |
| `hermetic-image-build` | Run the *build* deterministically (owner's "Phase 2"). | Root cause measured, blocked (§5) |

---

## 2. Ground truth: what the podman install on this box actually exposes

### 2.1 Store layout

`~/.local/share/containers/storage/` — `graphDriver: overlay`, backing filesystem btrfs, native
overlay diff `true`. Contents:

```
db.sql                 348160 bytes   libpod container-state DB (databaseBackend: sqlite)
overlay/               per-layer dirs: diff/ empty/ link merged/ work/
overlay-images/        images.json  + per-image big-data dirs
overlay-layers/        layers.json
overlay-containers/    per-container userdata/
```

Note the split: **libpod state is sqlite; containers/storage metadata is still JSON** on this
install. A design that parses `db.sql` and a design that parses `images.json` are reading *different
authorities*. Neither is a stable interface (§3.2).

### 2.2 Getting a rootfs out of the store — three mechanisms, all measured

| Mechanism | Works rootless? | Visible to a process outside podman? | Copy cost |
| --- | --- | --- | --- |
| `podman image mount <id>` | **No** — `Error: cannot run command "podman image mount" in rootless mode, must execute 'podman unshare' first` | — | — |
| `podman unshare podman image mount <id>` | Yes → `<store>/overlay/<layer>/merged` | **No.** Confined to that `podman unshare` mount namespace; from outside the directory is empty (verified twice) | none |
| `podman image inspect --format '{{json .GraphDriver}}'` → overlay-mount the dirs yourself | Yes | Yes, inside your own userns | none |

The third row is the important one. For a 3-layer image it returns exactly the mount arguments:

```json
{"Name":"overlay","Data":{
  "LowerDir":"…/264a9987…/diff:…/f103cd12…/diff",
  "UpperDir":"…/3475c9b1…/diff",
  "WorkDir":"…/3475c9b1…/work"}}
```

and an unprivileged overlay mount of those `diff` dirs succeeds on this kernel:

```
$ unshare -Umr mount -t overlay overlay -o lowerdir=<diff>,upperdir=…,workdir=… /tmp/ovtest/merged
OVERLAY_MOUNT_OK
$ head -2 /tmp/ovtest/merged/etc/os-release
PRETTY_NAME="Ubuntu 24.04.4 LTS"
```

Hermit **already creates a user namespace and a mount namespace** for every run
(`reverie-process` `Namespace::USER | MOUNT`, see §7), so this is a natural fit: hermit can mount the
podman layers itself and never copy.

### 2.3 The libpod REST API

`podman info` reports `remoteSocket: {exists: true, path: /run/user/212630/podman/podman.sock}` —
**but the socket file did not exist**, and `systemctl --user is-active podman.socket` was `inactive`.
`podman info`'s `exists` field reflects the *configured* path, not liveness. This is a textbook
proxy-binding trap: a consumer that keys on `remoteSocket.exists` will believe a dead service is up.

After `systemctl --user start podman.socket` the socket appears and the API answers:

```
GET http://d/v5.0.0/libpod/images/json  (--unix-socket …/podman.sock)  ->  1 image, Id 097d2bc97c7d…
```

So `libpod-rest-client` is available, but **it must be started**, and starting it is a user-scoped
side effect a hermit invocation should not silently perform.

### 2.4 The OCI-runtime seam is interceptable

Installing a shim as podman's runtime works rootless, and the protocol podman actually speaks is
small. Captured verbatim from `/tmp/fakeruntime.log` during
`podman --runtime /tmp/fakeruntime.sh run --rm 097d2bc97c7d /bin/echo hello-from-shim`:

```
--systemd-cgroup create --bundle <store>/overlay-containers/<cid>/userdata \
                        --pid-file /run/user/212630/containers/overlay-containers/<cid>/userdata/pidfile <cid>
start <cid>
delete --force <cid>
```

The bundle directory contains a full `config.json` (11 762 bytes) at `create` time. It does **not**
exist at `podman create` time — only when the runtime is invoked (verified: `podman create` leaves
`userdata/` holding only `artifacts secrets shm`).

This is what makes `podman ps` visibility achievable: a runtime that podman drove is a container
podman knows about.

---

## 3. Question 1 — podman as a **library**, or shell out to the binary?

**Answer: neither "library" in the linking sense. Use the CLI as the contract, with the REST API as
an optional faster transport. Do not link `containers/storage` or `containers/image`.**

### 3.1 What "as a library" would actually cost

`containers/image` and `containers/storage` are Go. Consuming them from hermit's Rust binary means
cgo + a Go toolchain in hermit's build, a c-archive shim, and a static version pin of the store
implementation compiled into hermit. Meanwhile the store format on disk is versioned and *already
heterogeneous on this very box* (libpod on sqlite, c/storage on JSON — §2.1). A statically linked
store reader is a compatibility liability that gets worse every podman upgrade, for a component
hermit invokes at most once per run.

Measured cost of the current shell-out, for an 80 MB Ubuntu image, so the "forking is slow" objection
can be priced rather than assumed:

| | wall |
| --- | --- |
| `hermit run --image <64-hex>` cold (buildah from + mount + `cp -a` + hermit run of `/bin/true`) | **0.578 s** |
| same, warm (cache hit) | **0.025 s** |

*(Caveat on the cold number: `cp -a` from GNU coreutils defaults to `--reflink=auto` and the store is
on btrfs, so the copy is CoW. On a non-reflink filesystem the cold number would be dominated by a
real 80 MB copy. The `podman-store-overlay` design in §4 removes this dependence entirely.)*

0.578 s of fork overhead, once, against a hermit run — this does not justify a cgo build.

### 3.2 What is actually a stable interface

Ranked by how much they bind hermit to podman internals:

| Surface | Binds hermit to | Verdict |
| --- | --- | --- |
| `podman image inspect --format '{{json .GraphDriver}}'`, `podman image inspect --format '{{json .Config}}'` | podman's documented CLI + Go template output | **Use this.** Public, versioned, human-auditable. |
| libpod REST `/v5.0.0/libpod/images/{name}/json` | podman's versioned HTTP API | Use as an optimization when the socket is *already* running; never auto-start it. |
| Parsing `overlay-images/images.json`, `overlay-layers/layers.json` | containers/storage on-disk format | **Do not.** Version-fragile; this box already shows two metadata backends coexisting. |
| cgo-linking `containers/storage` | a compiled-in c/storage version | **Do not.** |

### 3.3 Rust-side availability, checked offline

Because egress is down this is a real constraint, not a hypothetical:

| Crate | In `~/.cargo/registry/cache`? | In index cache? |
| --- | --- | --- |
| `oci-spec` 0.10.0 | **yes** | yes |
| `oci-distribution` 0.11.0 | **yes** | no |
| `oci-unpack` 0.1.1 | **yes** | no |
| `podman-api` | no | no |
| `oci-client` | no | no |
| `ocidir` | no | no |
| `containers-image-proxy` | no | no |

So the three crates a "pure Rust OCI" path would want (`oci-spec` for the config/manifest types,
`oci-distribution` for registry pulls, `oci-unpack` for layer extraction) are **already vendorable
today, offline**. A Rust *podman client* crate is not available and, on the evidence above, is not
wanted anyway — the CLI is the contract.

**Practical consequence:** `oci-spec` is worth adopting *now*, independently of everything else, to
replace the current ad-hoc `buildah inspect --format '{{...}}'` string scraping in `image.rs` with
typed deserialization of the image config. That is a small, self-contained cleanup.

---

## 4. Question 2 — how does building on podman change the design?

Less than expected on the *pull* side (already done), and a lot on the *materialize* side.

### 4.1 Side-by-side

| Dimension | `oci-rootfs-materialize` (landed) | `podman-store-overlay` (proposed) |
| --- | --- | --- |
| Pull / on-disk management | `buildah from` → containers/storage | `podman pull` → containers/storage (**same store**) |
| Getting a rootfs | `buildah mount` inside `buildah unshare`, then `cp -a` the merged tree to `~/.cache/hermit/oci-rootfs/<key>/rootfs` | `podman image inspect` → `LowerDir`/`UpperDir`; hermit mounts overlay in its own userns |
| Disk cost | Second full copy per image. **Measured: 814 MB in `~/.cache/hermit/oci-rootfs/` for 5 cached rootfses** | Zero. Reuses podman's layers |
| Fork count per run | 1 (`buildah unshare` running a shell script) | 1 (`podman image inspect`), or 0 with the REST socket |
| Guest writes | rootfs bind-mounted read-only + tmpfs `/tmp` (hardened in `c68f150aa`) | overlay `upperdir` per run — a proper CoW upper, and it can be a tmpfs for a fully throwaway run |
| Cache invalidation | hermit's own marker file, keyed on the **reference string** (defect D1, §8) | podman's store is the cache; hermit holds no second copy to invalidate |
| Failure mode when podman upgrades | buildah CLI contract | podman CLI contract + overlay dir layout |

### 4.2 The seam does not move

`materialize_rootfs(image_ref) -> rootfs dir` — the seam the prototype deliberately isolated — is
still the right seam. `podman-store-overlay` changes its *body*, not its signature, except that the
returned rootfs now has a lifetime tied to a mount that must be torn down. That argues for changing
the return type to an RAII guard rather than a bare `PathBuf`.

### 4.3 What building on podman does *not* buy you

- **It does not give hermit a network stack.** netavark/pasta/aardvark-dns are wired to podman's
  container lifecycle, not to a rootfs. Under `podman-store-overlay` hermit's guest keeps hermit's
  own network namespace (§7).
- **It does not give `podman ps` visibility.** Only `hermit-as-oci-runtime` does (§6.3).
- **It does not make builds reproducible.** That is orthogonal (§5).

---

## 5. Question 3 — how the two plans relate to Phase 2 (hermeticize the podman build)

### 5.1 The relationship, stated plainly

`oci-rootfs-materialize` and `podman-store-overlay` are both **image consumers**. `hermetic-image-build`
is an **image producer**. They meet at exactly one place: the store. A hermetic build writes an image
into containers/storage; either consumer then runs it. **Neither consumer design constrains the
producer design, and Phase 2 does not have to wait for either.**

The one coupling that matters is the opposite of the obvious one: **a hermetic build makes the
consumer's caching sound.** Right now the `--image` cache is keyed on a mutable reference string
(defect D1). If builds produce content-addressed, reproducible digests, digest-keyed caching becomes
both correct and useful.

### 5.2 Root cause of build nondeterminism, measured

Two builds of an identical 3-instruction Containerfile (`FROM <local ubuntu>`, two `RUN echo`s),
`--no-cache`, back to back:

```
build1: id=c8d14985… layers=[f103cd12…, 576b1b70…, e792edbc…]
build2: id=50644fb4… layers=[f103cd12…, c66cbe6d…, e792edbc…]
                              ^same base   ^DIFFERS   ^SAME
```

The middle layer diverged and the top layer did **not**. That asymmetry is the whole diagnosis. The
layer diff dirs contain byte-identical content with different mtimes:

| Layer | file | size | mtime |
| --- | --- | --- | --- |
| build1 L2 | `layer1.txt` | 10 | 2026-08-05 **20:08:59** |
| build2 L2 | `layer1.txt` | 10 | 2026-08-05 **20:09:00** |
| build1 L3 | `layer2.txt` | 10 | 2026-08-05 20:09:00 |
| build2 L3 | `layer2.txt` | 10 | 2026-08-05 20:09:00 |

**Layer digests are a function of wall-clock file mtime at one-second granularity.** Build 1's
`layer1.txt` landed at :59 and build 2's at :00, so those layers diverged; both `layer2.txt` writes
happened to land inside the same second, so those layers collided and matched *by luck*. A build that
"reproduced" is not evidence of hermeticity — it may just have been fast enough.

A second, independent source was visible before any experiment: the five pre-existing images in the
store all reference **the same single layer** `f103cd12…` yet have five different image IDs, with
`Created` timestamps 150 ms apart. **The config `created` field alone changes the image ID even when
every layer is identical.**

### 5.3 Podman's own reproducibility knobs — measured, and one of them is a trap

| Invocation | image ID run A | image ID run B | reproducible? |
| --- | --- | --- | --- |
| plain `podman build --no-cache` | `c8d14985…` | `50644fb4…` | **no** |
| `SOURCE_DATE_EPOCH=1700000000` (env) | `3bbe9ca7…` | `6aaf2024…` | **no** — but `Created` *was* pinned to 2023-11-14 |
| `podman build --timestamp 0` | `c79c209c…` | `c79c209c…` | **yes** |
| `podman build --source-date-epoch 1700000000 --rewrite-timestamp` | `e14a9744…` | `e14a9744…` | **yes** |

The `SOURCE_DATE_EPOCH` row is the trap, and it is the one most likely to be adopted by reflex
because it is the RB-canonical variable: it pins the *image info* timestamp — visibly, so it looks
like it worked — while leaving layer mtimes on wall clock. The image ID still moves. Anyone wiring
reproducible container builds here must use `--timestamp`, or `--source-date-epoch` **together with**
`--rewrite-timestamp`.

`podman build --help` confirms the intended semantics:
`--source-date-epoch` = "set new timestamps in image **info**"; `--rewrite-timestamp` = "set
timestamps in **layers**"; `--timestamp` = "image info **and** layer".

### 5.4 Where hermit adds value that `--timestamp` cannot

`--timestamp` normalizes timestamps *after the fact*. It does nothing about nondeterminism **inside**
a `RUN` step: a build that embeds `date`, `$RANDOM`, a PID, a hash-map iteration order, or a parallel
`make` race produces different *content*, and `--timestamp` faithfully preserves the difference.
That is precisely hermit's domain, and it is the real Phase-2 thesis:

> `--timestamp` makes the *packaging* reproducible. Hermit makes the *computation* reproducible.
> Reproducible packaging of a nondeterministic computation is a reproducible lie.

This is also the cleanest dovetail with the RB work: RB needs `RUN` steps to be deterministic
functions of their inputs, which is exactly what `hermit run` provides.

### 5.5 Current blocker: `podman build` under hermit does not work yet

Attempted, and characterized in stages. Native baseline for a `FROM scratch` + `COPY` build into an
isolated store: **5.901 s**.

| Attempt | Result |
| --- | --- |
| `hermit run -- podman build …` | `Error: creating runtime static files directory "/var/lib/containers/storage/libpod": permission denied` |
| + `--root`/`--runroot` | `Error: creating runtime temporary files directory: mkdir /run/libpod: permission denied` |
| + `--tmpdir` | `Error: configure storage: overlay: failed to make mount private: … operation not permitted` |
| + `--storage-driver vfs` | `Error: open /run/lock/netavark.lock: permission denied` |
| + `--network none` | same netavark lock error |
| `hermit run --no-namespace -- podman build …` | got past all path errors, then **stalled: killed by a 600 s timeout (`exit 124`) having produced no output**, versus 5.901 s native — ≥ 101× and never completed |

The stall point is narrower than "the build is slow". After the 600 s kill, the isolated store
contained only `libpod/`, `volumes/`, `db.sql`, `db.sql-journal` — **no vfs layer directories at
all**. podman never reached the first build step; it hung during store/database initialization. That
is a much more tractable target than "make a whole build pipeline fast under ptrace", and it should
be diagnosed with `hermit strace` before any Phase-2 design work is committed to.

**Root cause of the whole first column:** hermit's container calls `map_root()`, so the guest's euid
is 0. Verified: `hermit run -- /usr/bin/id` → `uid=0(root) gid=0(root)`. podman decides
rootless-vs-rootful on euid, so under hermit it takes the **rootful** path and hardcodes `/var/lib/…`,
`/run/libpod`, `/run/lock/netavark.lock` — none of which a ns-root-only process can write. Each
individual path is overridable, but they are a long tail, and the last one (`netavark`) is not
exposed as a per-invocation flag.

Two things worth recording from these runs:

1. **Hermit's virtual clock reached podman.** Every failing run logged
   `time="2025-12-31T16:00:00-08:00"` — podman's own Go logger, stamped with hermit's deterministic
   time. This is direct evidence that the §5.2 mtime root cause is exactly the thing hermit
   virtualizes, i.e. that a working `podman build` under hermit would be reproducible *without*
   `--timestamp`.
2. Podman under hermit warns `Using cgroups-v1` although the host is cgroup v2 — hermit's `/proc`
   view misleads podman's cgroup probe.

**Recommended Phase-2 sequencing given this:** do not start by trying to run the full `podman build`
pipeline under hermit. Start with `hermit run` as the executor of individual `RUN` steps
(`buildah run` is already a separate, much smaller process than `podman build`), or drive
`buildah`'s step-by-step API from a hermit-aware driver. Attacking `podman build` whole means fighting
podman's rootful-path assumptions **and** its fork/exec of crun **and** its overlay mounts, all at
once, under a ptrace backend. The ≥80× wall-clock observation says that path needs a real perf
investigation before it is a plan.

---

## 6. Question 4 — the capabilities problem

### 6.1 The premise, and the measurement that refutes it

The prompt states: *"hermit needs PMU/CPUID-interception/dev-kvm, hard to get in a podman container
without ROOT."*

Measured with a purpose-built C probe (`/tmp/probe.c`, source in §10) across five contexts. All rows
are **rootless** — the invoking user is uid 212630 with `CapEff: 0x0` on the host.

| Context | `/dev/kvm` | `perf_event_open` (HW instructions) | `arch_prctl ARCH_SET_CPUID` | `ptrace(TRACEME)` | `unshare(NEWUSER)` | `CapEff` |
| --- | --- | --- | --- | --- | --- | --- |
| host | OK | OK | OK | OK | OK | `0x0` |
| `podman unshare` | OK | OK | OK | OK | OK | `0x1ffffffffff` (all 41) |
| `podman run` (defaults) | **ENOENT** | **EPERM** | OK | OK | OK | `0x800405fb` (14) |
| `podman run --device /dev/kvm --security-opt seccomp=unconfined` | **OK** | **OK** | OK | OK | OK | `0x800405fb` (14) |
| `podman run --privileged` | OK | OK | OK | OK | OK | `0x1ffffffffff` |

**The premise is false.** A rootless container gets PMU, KVM and CPUID faulting with two flags and no
host root, and does not even need `--privileged`. Exactly two defaults were in the way:

- `/dev/kvm` is simply **absent from the container's `/dev`** — `--device /dev/kvm` bind-mounts the
  host node in. Rootless works because the *host* uid can already open it; no new privilege is
  created.
- `perf_event_open` is denied by the **default seccomp profile** (`/usr/share/containers/seccomp.json`),
  not by capabilities — `perf_event_paranoid` is `1` in every context, including inside the container.
  `--security-opt seccomp=unconfined` (or a narrow custom profile allowing `perf_event_open`) lifts it.

Notably `arch_prctl(ARCH_SET_CPUID)` — CPUID faulting — works **everywhere, unconditionally**,
including a default container. It was never at risk.

### 6.2 The corollary that matters more

**`podman unshare` is strictly better than a container for hermit's purposes**, and it is what the
"can't get capabilities" intuition should have pointed at all along. Inside it:

- euid 0 with **all 41 capabilities**,
- **full host device access retained** (`/dev/kvm` opens),
- **full PMU access retained** (`perf_event_open` succeeds — no container seccomp profile applies),
- host PID namespace (`pid:[4026531836]`, same as host — verified),
- and the podman overlay mounts are resolvable.

So the three-way choice for "where does hermit's guest actually execute" is:

| Placement | KVM / PMU | Isolation | `podman ps` |
| --- | --- | --- | --- |
| plain host process (today) | full | hermit's own namespaces | no |
| inside `podman unshare` | full | hermit's own namespaces, plus podman's userns | no |
| inside a real `podman run` container | needs 2 flags | podman's full stack | yes |

### 6.3 On the owner's "fine if it doesn't show in `podman ps`"

Accepted as a valid position — but it is now a *choice*, not a forced concession, and there is a
cheap way to have both. §2.4 showed `podman --runtime <shim>` is interceptable rootless with a
three-verb protocol. In that arrangement:

- podman does image resolution, bundle assembly, networking, cgroups, and bookkeeping;
- **the runtime process — hermit — runs outside the container, as the invoking user**, and *creates*
  the container's namespaces itself. It therefore has whatever the host user has: KVM, PMU, ptrace.
  The capability question disappears rather than being negotiated;
- the container appears in `podman ps`, `podman logs`, `podman stats`, `podman stop`.

The cost is real and should not be understated: hermit would have to honour a `config.json` it did
not author (mounts, rlimits, seccomp, capability sets, cgroup paths, hooks), implement
`create`/`start`/`state`/`kill`/`delete` with the correct pidfile and conmon handshake semantics, and
keep that contract as the OCI runtime spec evolves. That is a genuine sub-project, not a shim — which
is why it is sequenced last in §9.

---

## 7. The feature/namespace gap matrix (owner's refinement)

The owner asked for each podman feature to be classified as **(a)** already covered by hermit
virtualization, **(b)** a genuine functional gap, or **(c)** worth double protection, where (c) is
justified only by security defence-in-depth.

### 7.1 First, a measurement warning

Probing namespaces *from inside* a hermit guest gives wrong answers. Compare, for the same run:

| ns | guest's own `readlink /proc/self/ns/*` | observed from the host on the guest PID | truth |
| --- | --- | --- | --- |
| uts | `uts:[4026531838]` (= host's inode) | `uts:[4026539094]` | **separate** |
| pid | `pid:[4026531836]` (= host's inode) | `pid:[4026539095]` | **separate** |
| user | `user:[4026531837]` (= host's inode) | `user:[4026539092]` | **separate** |

Detcore virtualizes namespace inode numbers (it must — raw inode numbers are nondeterministic). So
`/proc/self/ns/*` read inside the guest is a **proxy that lies about the thing it names**. Every row
below is from the *host's* view of the guest PID.

### 7.2 The matrix

Measured: `hermit run` (default) with a live guest, observed from the host; `podman run` observed
from inside a container.

| Facility | hermit today | podman today | Class | Notes |
| --- | --- | --- | --- | --- |
| **PID namespace** | **separate** (`pid:[4026539095]`) | separate | **(a)** | Hermit both unshares *and* virtualizes pids. Redundant but already paid for. |
| **User namespace** | **separate**, euid 0 via `map_root()` | separate, euid 0 | **(a)** | |
| **Mount namespace** | **separate** | separate | **(a)** | |
| **UTS namespace** | **separate**; hostname `hermetic-container.local`, domainname `local` | separate; hostname = short container id | **(a)** | Hermit's is *more* deterministic (fixed string vs random id). |
| **Network namespace** | **separate** (`net:[4026539096]`) | separate + netavark/pasta plumbing | **(a)** for isolation, **(b)** for *function* | Hermit isolates but provides no configured network. Anything needing DNS or outbound connectivity is a gap. |
| **IPC namespace** | **SHARED with host** (`ipc:[4026531839]`) | separate | **(b)** | Guest SysV IPC / POSIX mqueues land in the host's IPC namespace: a cross-run and cross-agent collision surface, i.e. a *determinism* bug, not just a security one. Cheap to fix — `Namespace::IPC` already exists in `reverie-process`. |
| **cgroup namespace** | **SHARED with host** | separate (guest sees `0::/`) | **(b)** for view, **(c)** for enforcement | The guest reads the *host* cgroup path (`…/3pai_sandbox.slice/run-p864027-i5035501.scope`) — that string is host-dependent and leaks into any guest that reads `/proc/self/cgroup`, so it is a determinism leak. Resource *enforcement* is separate and is (c). |
| **cgroup resource limits** | none applied by hermit | applied via systemd cgroup manager | **(c)** | Already partly handled out-of-band by the `ci-hub validate-run` boxing path; hermit itself imposing limits would be double protection. |
| **Capabilities** | **all 41** (`CapEff 0x1ffffffffff`) | **14** (`0x800405fb`) | **(c)** | Pure defence-in-depth. Hermit's guest is ns-root with every capability in its userns; podman drops to a curated set. No determinism impact. |
| **`no_new_privs`** | **1** | **0** | (a), hermit stricter | Side effect of detcore's seccomp filter. |
| **seccomp filter** | present, 1 filter — **detcore's syscall trap** | present, 1 filter — **the security denylist** | **(c)** | Both report `Seccomp: 2, Seccomp_filters: 1`. **These are different authorities with identical-looking evidence.** A check that reads "seccomp is on" cannot tell a determinism mechanism from a security mechanism. |
| **`/dev` contents** | **default run: the entire host `/dev`** (`autofs`, `btrfs-control`, `cpu`, `hpet`, `hwrng`, `i2c-*`, …). **`--image` run: completely empty — `/dev/null` does not exist** | 15 curated nodes: `null full random urandom zero tty ptmx pts shm mqueue fd std{in,out,err} core` | **(b)** — both directions | The `--image` case is a hard functional blocker: `sh -c` fails with `cannot create /dev/null: Read-only file system`. The default case is simultaneously a determinism leak (`/dev/hwrng`, `/dev/hpet`) and a security hole. This is the single highest-value item in the table. |
| **rlimits** | inherited | set from config | (c) | |
| **Image rootfs (read-only) + tmpfs `/tmp`** | present (`c68f150aa`) | present | **(a)** | |
| **Image `Config.Env` / `WorkingDir` applied** | present | present | **(a)** | |
| **Registry pull / layer store** | via buildah → containers/storage | native | **(a)** | Literally the same store. |
| **Volumes / bind mounts** | `--mount`, `--bind` | `-v`, `--mount` | **(a)** | |
| **Image signature / policy verification** | none | `containers-policy.json` | **(b)** | Not exercised on this box. |
| **`podman ps` / logs / stats / stop** | none | native | **(b)** if wanted | Only `hermit-as-oci-runtime` closes it. |

### 7.3 Reading of the matrix

The honest summary is that **hermit's namespace coverage is already good** — 5 of 7 namespaces, plus
a read-only image rootfs and applied image config. The gaps that matter are not the ones the "which
namespaces are we missing" framing would surface first:

- The **worst gap is `/dev`**, which is not a namespace at all, and it is broken in *both*
  directions at once (too much in the default path, nothing in the `--image` path).
- **IPC and cgroup namespaces** are worth adding for **determinism**, not for security — a shared IPC
  namespace across concurrent hermit runs on an 18-agent box is a cross-run interference channel, and
  a shared cgroup namespace leaks a host-specific path string into the guest.
- **Capabilities and cgroup limits** are the only genuinely (c) items: security-only, no determinism
  argument, defensible to defer.

---

## 8. Defects and gaps found (with locations)

| # | Finding | Where | Severity |
| --- | --- | --- | --- |
| **D1** | **Rootfs cache key is the reference *string*, not the image identity.** `let key = Digest::new(image_ref.as_bytes())`. `hermit run --image nixos/nix:latest` caches under a key derived from `"nixos/nix:latest"`; when that tag moves, the stale rootfs is silently reused and the "inputs pinned by digest" guarantee is void. The cache dir listing shows this live: `docker_io_nixos_nix_latest` sits beside digest-keyed entries. Proxy-binding failure — the key does not carry the condition it claims. | `hermit-cli/src/bin/hermit/image.rs:61` | **high** |
| **D2** | **`--image` guest has an empty, read-only `/dev`; `/dev/null` does not exist.** `sh -c` immediately fails with `cannot create /dev/null: Read-only file system`. Nearly every real program needs `/dev/null`. | `container.rs::image_container` | **high** |
| **D3** | **Default `hermit run` passes the entire host `/dev` through**, including `/dev/hwrng`, `/dev/hpet`, `/dev/cpu`, `/dev/btrfs-control`. Determinism leak and isolation hole. | `container.rs::default_container` | **high** |
| **D4** | IPC namespace shared with host → cross-run SysV IPC / mqueue interference between concurrent hermit runs. `Namespace::IPC` already exists in `reverie-process`. | `container.rs` | medium |
| **D5** | cgroup namespace shared with host → guest reads a host-specific cgroup path from `/proc/self/cgroup`. | `container.rs` | medium |
| **D6** | Guest capability set is all 41 caps; podman drops to 14. Security-only. | `container.rs` | low |
| **D7** | Image config is scraped with `buildah inspect --format '{{...}}'` string templates rather than typed OCI deserialization. `oci-spec` 0.10.0 is already in the local cargo cache. | `image.rs:259-260` | low (robustness) |
| **D8** | `podman info` reports `remoteSocket.exists: true` while the socket file does not exist and `podman.socket` is inactive. Any consumer keying on that field is reading a configured path, not liveness. | podman, not hermit | informational |
| **D9** | **Environment: `libunwind` is not installed on this box** (`ldconfig -p` empty, `rpm -q libunwind` → not installed), so **every** `hermit` binary in every worktree fails at startup with `libunwind-x86_64.so.8: cannot open shared object file`. This is fleet-wide, not specific to this task. Worked around locally *without* mutating the host: `dnf download libunwind` (internal repos are reachable even with external egress 403) + `rpm2cpio | cpio -idmv` into `/tmp/lu`, then `LD_LIBRARY_PATH=/tmp/lu/usr/lib64`. A permanent fix (`dnf install libunwind`) needs an owner decision since it mutates the shared box. | host | **fleet-wide blocker** |

---

## 9. Question 5 — the compromise, and the recommended plan

### 9.1 The compromise as stated

*"hermit as a full standalone OCI system that ALSO discovers and runs already-pulled podman/docker
images."*

**Pros**

- **The discovery half already works.** No new code was needed to run a local podman image under
  hermit (§0). The store is shared; buildah reads it; hermit reads it through buildah.
- **No podman required at run time** for the standalone half, so hermit stays usable on hosts without
  a container stack — which matters for CI images and for the KVM backend's future
  present-rootfs-to-VM path.
- **Offline-viable in Rust**: `oci-spec` + `oci-distribution` + `oci-unpack` are all already in the
  local cargo cache (§3.3), so a standalone pull/unpack path could be built with no egress.
- Keeps hermit's determinism story self-contained and auditable: one codebase owns "what files the
  guest sees".
- Degrades gracefully: discovery is a fast path, standalone is the fallback.

**Cons**

- **Two code paths for the same seam** means two sets of bugs and a doubled test matrix — and the
  discovery path's bugs will be podman-version-dependent, which is the expensive kind.
- **Re-implements the part that is genuinely hard and genuinely done**: registry auth, mirrors,
  `containers-policy.json` signature verification, manifest lists / multi-arch selection, zstd:chunked
  and partial-pull, credential helpers. `oci-distribution` gives the happy path; the corporate-registry
  long tail is what podman actually buys you.
- **A second image store** to garbage-collect. The current cache is already 814 MB for five rootfses,
  with no eviction policy.
- **Docker discovery is untestable here** — there is no docker on this box, so any docker-store support
  would ship unverified.
- Signature/policy verification not implemented in either path today (D-list gap).

### 9.2 Recommendation

Adopt the compromise, but **weight it heavily toward discovery** and make the standalone path an
explicit fallback rather than a co-equal implementation:

- **discovery is the default** whenever a containers/storage store is present;
- **standalone pull is opt-in** (`--image-source=direct`) for hosts with no container stack, and is
  built on `oci-spec`/`oci-distribution`/`oci-unpack` rather than hand-rolled;
- **do not implement docker-store discovery** until there is a host to test it on. Reading
  `/var/lib/docker` is a different, root-owned, driver-dependent layout; claiming support for it
  unverified is worse than not claiming it.

### 9.3 Proposed phasing

Ordered by value-per-risk, from the measurements above. Each phase is independently landable.

| Phase slug | Content | Why here |
| --- | --- | --- |
| `image-dev-and-cache-correctness` | Fix **D2** (populate a minimal deterministic `/dev`: `null full zero random urandom tty ptmx` + `/dev/shm`), **D3** (stop passing host `/dev` through the default path), **D1** (key the cache on the resolved image *digest*, resolved via `podman image inspect --format '{{.Digest}}'`, not the reference string). | D2 makes `--image` usable for real programs; D1 makes the determinism claim true. Both are small and both are prerequisites for anything else being trustworthy. |
| `hermit-oci-subcommand` | `hermit oci ls` / `hermit oci inspect <ref>` / `hermit oci run <ref> -- cmd`, listing what is discoverable in the local store and what hermit will do with it. Typed config via `oci-spec` (**D7**). | Pure UX/discovery on top of what already works. No new mechanism. Gives the owner the visible "`hermit oci`" surface asked for. |
| `podman-store-overlay` | Replace copy-out with overlay-mount-in-place from `podman image inspect`'s `LowerDir`/`UpperDir`; per-run `upperdir`. Change the seam's return type to a mount guard. | Removes the 814 MB second store and the reflink dependence; gives a real CoW upper layer, which is the top follow-up from the prototype write-up. Mechanism already verified. |
| `guest-namespace-completion` | Add `Namespace::IPC` and `Namespace::CGROUP` (**D4**, **D5**); optionally drop capabilities to podman's set (**D6**). | Determinism first, security second — in that order, per §7.3. |
| `hermetic-image-build` | First diagnose the store-init stall with `hermit strace` (§5.5). Then start with `hermit run` executing individual `RUN` steps via `buildah run`, **not** `podman build` whole. Bind the result to `--timestamp`/`--rewrite-timestamp` for packaging and to hermit for computation. | Highest value for the RB dovetail, highest risk, and currently blocked before the first build step. Sequenced after the cheap wins so it is not on anyone's critical path. |
| `hermit-as-oci-runtime` | Implement `create`/`start`/`state`/`kill`/`delete` against podman's bundle so hermit runs appear in `podman ps`. | Genuinely useful, genuinely a sub-project (§6.3). Only worth starting once the owner confirms `podman ps` visibility is a requirement rather than a nicety — the capability argument that seemed to force it has been refuted. |

---

## 10. Reproduction

Environment prerequisite (see **D9**): hermit binaries do not start without libunwind.

```bash
mkdir -p /tmp/lu && cd /tmp/lu
dnf download libunwind && rpm2cpio libunwind-1.8.0-4.el9.x86_64.rpm | cpio -idmv
export LD_LIBRARY_PATH=/tmp/lu/usr/lib64
H=/home/newton/work/dev-hermit/hermit/target/debug/hermit
```

**Shared store (§0):**
```bash
podman info  | grep -A2 '^store:' ; buildah info | python3 -c 'import json,sys;print(json.load(sys.stdin)["store"]["GraphRoot"])'
$H run --image 097d2bc97c7d -- /bin/cat /etc/os-release      # Ubuntu 24.04 on a CentOS host
```

**Overlay-in-place (§2.2):**
```bash
podman inspect <image> --format '{{json .GraphDriver}}'
D=<a layer diff dir>; mkdir -p /tmp/ovtest/{upper,work,merged}
unshare -Umr mount -t overlay overlay \
  -o lowerdir=$D,upperdir=/tmp/ovtest/upper,workdir=/tmp/ovtest/work /tmp/ovtest/merged
```

**Runtime interception (§2.4):** a shim that logs `"$@"`, copies `<bundle>/config.json`, then
`exec /usr/bin/crun "$@"`, invoked as `podman --runtime /tmp/fakeruntime.sh run --rm <image> /bin/echo hi`.

**Capability probe (§6.1):** `/tmp/probe.c` — opens `/dev/kvm`; issues
`perf_event_open(PERF_TYPE_HARDWARE/PERF_COUNT_HW_INSTRUCTIONS, pid=0, cpu=-1)`;
`arch_prctl(ARCH_SET_CPUID, 0)`; `ptrace(PTRACE_TRACEME)`; `unshare(CLONE_NEWUSER)`; prints
`CapEff`/`CapBnd`. Built dynamically (`gcc -O0`; host has no static libc) and bind-mounted into the
container — the Ubuntu 24.04 image's glibc 2.39 loads a binary built against host glibc 2.34. Run as
`/tmp/probe`, `podman unshare /tmp/probe`, and
`podman run --rm -v /tmp/probe:/probe:ro <image> /probe` with the flag variants in the table.

**Build determinism (§5.2–5.3):**
```bash
printf 'FROM <local-image-id>\nRUN echo layer-one > /layer1.txt\nRUN echo layer-two > /layer2.txt\n' > Containerfile
for i in 1 2; do podman build --no-cache -t t:$i -f Containerfile . ; done
for i in 1 2; do podman inspect t:$i --format '{{.Id}} {{.RootFS.Layers}}'; done
# then repeat with: --timestamp 0 | SOURCE_DATE_EPOCH=… | --source-date-epoch … --rewrite-timestamp
# and compare the layer diff-dir mtimes via overlay-layers/layers.json
```

**Namespace observation (§7.1–7.2):** launch a CPU-bound guest under hermit in the background
(`hermit run -- /bin/sh -c 'i=0; while [ $i -lt 4000000 ]; do i=$((i+1)); done'`), find the guest PID
with `ps -eo pid,ppid`, and compare `readlink /proc/<guest>/ns/*` **from the host** against
`/proc/self/ns/*`. Do **not** read `/proc/self/ns/*` inside the guest — detcore virtualizes those
inode numbers.

*(`sleep N` cannot be used to hold a hermit guest open: hermit virtualizes time, so `sleep 30`
returns immediately.)*

---

## 11. Limitations of this study

- **Backend:** ptrace only. No KVM or DBI run was attempted; the `--image` layer is backend-agnostic
  by construction (it configures namespaces before any backend attaches) but that was not re-verified
  here.
- **One image family:** all discovery/run measurements used the Ubuntu 24.04 images already in the
  local store, because egress is down. No multi-layer *registry* image, no manifest-list/multi-arch
  case, no signed image was exercised.
- **`podman build` under hermit is unresolved**, not proven impossible — the `--no-namespace` run was
  killed at 600 s (vs 5.901 s native) having never reached the first build step. The stated
  conclusion is only "diagnose the store-init stall before designing Phase 2." The stall was observed
  once, at one hermit SHA, on a loaded 316-core box; it has not been bisected or attributed.
- **No docker anywhere on this box**, so every claim about docker-store discovery is unverified by
  construction; that is why §9.2 recommends not shipping it.
- **Determinism levels:** no L1/L2 verification runs (`--strict --verify`) were performed in this
  study; the prototype write-up
  (`ai_docs/transient/hermit-container-runtime-prototype_20260730.md`) carries those for the landed
  `--image` path at hermit `c23152ff`.
- The 0.578 s cold-materialization number is btrfs-reflink-assisted and will not hold on other
  filesystems.

---

## 12. Implementation spec for the first phase (`hermit-oci-subcommand`)

Written for the sibling task `impl-hermit-oci-initial-phase-podman-store`, which is blocked on slot
allocation and on registry egress. Every podman command below was executed on this box; the exact
output shape is quoted so an implementer does not have to re-derive it.

### 12.1 Scope correction

The task title is "run images from the podman store, then podman-download". **The first half already
works on landed main** (§0) — this phase is about *exposing, naming and correcting* it, not building
it. The second half is `podman pull` plumbing that cannot be tested against a registry today (§12.5).

### 12.2 CLI surface

```
hermit oci ls                       # images discoverable in the local containers/storage
hermit oci inspect <ref>            # what hermit would do with <ref>: id, digest, Env, WorkingDir, rootfs source
hermit oci pull <ref>               # explicit download leg (podman pull), then report the resolved id
hermit oci run [run-flags] <ref> -- <cmd>    # resolve <ref> to an id, then the existing --image path
```

`hermit oci run` is deliberately a thin front for `hermit run --image <resolved-id>`: the resolution
step is the new part, and pinning to the **resolved 64-hex id** rather than the user's reference is
what fixes D1.

### 12.3 New module `hermit-cli/src/bin/hermit/oci.rs`

A `PodmanStore` type wrapping the CLI. All five probes verified:

| Purpose | Command | Verified output |
| --- | --- | --- |
| detect a store | `podman info --format '{{.Store.GraphRoot}}'` | `/home/newton/.local/share/containers/storage` |
| list | `podman images --format json` | JSON array with `Id`, `RepoTags`, `RepoDigests`, `Size` |
| resolve ref → id, **offline** | `podman pull --policy=never <ref>` | `1ee23df5e47b…` (64-hex, no network access) |
| identity | `podman image inspect <ref> --format '{{.Id}}'` / `'{{.Digest}}'` | `1ee23df5e47b…` / `sha256:291734d3…` |
| config | `podman image inspect <ref> --format '{{json .Config}}'` | `{"Env":[…],"Cmd":["/bin/bash"],"Labels":{…}}` |
| overlay dirs | `podman image inspect <ref> --format '{{json .GraphDriver}}'` | `{"Name":"overlay","Data":{"UpperDir":…,"LowerDir":…,"WorkDir":…}}` |

`podman pull --policy=never` is the right resolution primitive: it accepts any reference form (tag,
short id, full id, digest), returns the canonical id, and provably does not touch the network.

Deserialize `Config` with **`oci-spec` 0.10.0** (already in the local cargo cache, §3.3) rather than
scraping Go templates per field — this retires D7.

### 12.4 Changes to existing files

| File | Change | Fixes |
| --- | --- | --- |
| `image.rs:61` | Cache key becomes the **resolved image id**, not `Digest::new(image_ref.as_bytes())`. Resolution happens in `oci.rs` before the cache is consulted. | **D1** |
| `container.rs::image_container` | Populate a minimal deterministic `/dev` — `null full zero random urandom tty ptmx` plus `/dev/shm` — instead of an empty read-only dir. Without this, `sh -c` in any image fails with `cannot create /dev/null`. | **D2** |
| `container.rs::default_container` | Stop passing the host `/dev` through; use the same curated set. | **D3** |
| `image.rs` | Typed config via `oci-spec` instead of `buildah inspect --format` templates. | **D7** |

D2 is the one that gates usefulness: until it lands, `--image` cannot run an ordinary shell pipeline.

### 12.5 Test vectors that work with egress blocked

Registry pulls are refused by a **destination filter**, not a network outage:

```
$ with-proxy podman pull docker.io/library/busybox:latest
Error: … pinging container registry registry-1.docker.io: … Forbidden …
       registry-1.docker.io has not been allowlisted in filter {"agent_id":"agent:claude_code"}
```

Two offline substitutes, both verified:

1. **Store discovery + run:** `localhost/restored-ubuntu:24.04` (`1ee23df5e47b`, §13) is a real
   multi-purpose image in the store. `hermit run --image restored-ubuntu:24.04 -- /bin/cat /etc/os-release`
   prints `Ubuntu 24.04.4 LTS` on this CentOS host.
2. **Download leg without a registry:** `podman save --format oci-archive -o /tmp/oci-arch.tar <ref>`
   then `podman rmi <ref>` then `podman pull oci-archive:/tmp/oci-arch.tar`. This exercises the same
   `podman pull` machinery and **re-derived the identical image id** `1ee23df5e47b…` — a
   content-addressed round trip, which is exactly the property `hermit oci pull` needs to assert.

Getting `registry-1.docker.io` allowlisted for `agent:claude_code` would make the real download leg
testable; until then item 2 is the honest substitute and any "pull works" claim must say so.

## 13. Side effects this study had on shared state

Recorded so the next reader is not misled by a store that no longer matches §2.

1. **The five pre-existing images in the shared rootless podman store were destroyed by this study.**
   Cleaning up the eight test images with `podman rmi -f` cascaded through the shared ancestry —
   the test images were built `FROM` the pre-existing base — and removed all five images
   (`097d2bc`, `ca68f64`, `1556f29`, `d63cc8f`, `7fe1b4d`) and their single shared layer
   `f103cd12`. This was an error in the cleanup, not a pre-existing condition. All five were
   untagged `<none>:<none>` intermediates from one 2026-08-03 build, differing only in accumulated
   `ENV` lines.

   **Remediated:** the layer *content* survived in hermit's own rootfs cache (plain `cp -a` copies,
   untouched by `podman rmi`). A working base was rebuilt from
   `~/.cache/hermit/oci-rootfs/ec05cc6…/rootfs` via
   `podman unshare tar --numeric-owner --owner=0 --group=0 … | podman import` with the original
   `ENV`/`CMD`/`LABEL` restored, tagged `localhost/restored-ubuntu:24.04` (`1ee23df5e47b`, 80.7 MB).
   Verified to run under both `podman run` and `hermit run --image`.

   **Not restored:** the original image IDs/digests, and original per-file ownership (the import
   flattened uids to `0:0` because host-uid-owned entries exceeded the rootless subuid range).
   Anything pinning one of those five image IDs will now miss.

2. **`systemctl --user start podman.socket`** was run (§2.3) and left running; it was `inactive`
   before. Stopping it restores the prior state.

3. `libunwind` was **not** installed; the workaround is confined to `/tmp/lu` and
   `LD_LIBRARY_PATH` (**D9**). The host was not mutated.

## 14. Publication status

This report could not be pushed or linked: box-wide external egress was returning 403 throughout.
Internal package repositories *were* reachable (that is how **D9** was worked around), so the outage
is external-only. When egress returns, this file should be committed to `rrnewton/dev-hermit:main`
and its URL sent to the owner.
