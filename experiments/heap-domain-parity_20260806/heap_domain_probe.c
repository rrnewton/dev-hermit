/* heap_domain_probe -- enumerate the HEAP DOMAIN from inside the guest.
 *
 * Implements the owner's definition operationally (Rule A of
 * ai_docs/heap-domain-definition-guest-allocated-pages-20260805.md):
 *
 *   a region is HEAP iff  anonymous AND not-executable AND readable
 *                         AND not the stack domain AND not kernel-special,
 *   with .bss removed by the file-backed-adjacency test.
 *
 * Running the SAME code inside the guest under every backend is what makes the
 * comparison mean something: the definition cannot drift between arms, because
 * there is only one implementation of it.
 *
 * SELF-REFERENCE IS THE TRAP THIS FILE IS BUILT AROUND. If the enumerator held
 * its own working buffers on the heap, the heap digest would hash the memory map
 * it just read -- and the map differs between backends by construction, so every
 * arm would differ for a reason that is purely an artifact of measuring. Hence:
 * every buffer here is static (.bss, which the .bss rule excludes), and the
 * enumeration path uses raw open/read/write with no stdio and no malloc. Nothing
 * allocated after the snapshot can perturb what is hashed.
 *
 * Output is one line per admitted region, addresses travelling WITH digests,
 * because the prediction under test is "same address, same contents" and a
 * digest-only record cannot express the address half.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <stdlib.h>

#define MAPS_BUF   (1 << 20)
#define LINE_BUF   4096
#define MAX_REGION 512

/* All working storage is static: it lands in .bss, which the domain rule
 * excludes, so the instrument never hashes itself. */
static char maps_buf[MAPS_BUF];
static char out_buf[LINE_BUF];

struct region {
    unsigned long start, end;
    char perms[8];
    char path[256];
};
static struct region regions[MAX_REGION];
static int region_count;

/* ---- output helpers (no stdio: stdio's first call mallocs a buffer) ---- */

static void emit(const char *s, size_t n) {
    ssize_t off = 0;
    while ((size_t)off < n) {
        ssize_t w = write(1, s + off, n - off);
        if (w <= 0) return;
        off += w;
    }
}
static void emit_str(const char *s) { emit(s, strlen(s)); }

static int put_hex(char *dst, unsigned long v, int width) {
    static const char digits[] = "0123456789abcdef";
    char tmp[32];
    int n = 0;
    if (v == 0) tmp[n++] = '0';
    while (v) { tmp[n++] = digits[v & 0xf]; v >>= 4; }
    while (n < width) tmp[n++] = '0';
    for (int i = 0; i < n; i++) dst[i] = tmp[n - 1 - i];
    return n;
}
static int put_dec(char *dst, unsigned long v) {
    char tmp[32];
    int n = 0;
    if (v == 0) tmp[n++] = '0';
    while (v) { tmp[n++] = (char)('0' + (v % 10)); v /= 10; }
    for (int i = 0; i < n; i++) dst[i] = tmp[n - 1 - i];
    return n;
}

/* ---- FNV-1a 64: deterministic, dependency-free, order-sensitive ---- */
static uint64_t fnv1a(const unsigned char *p, size_t n) {
    uint64_t h = 1469598103934665603ULL;
    for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211ULL; }
    return h;
}

/* ---- /proc/self/maps snapshot, taken with no allocation ---- */

static size_t read_maps(void) {
    int fd = open("/proc/self/maps", O_RDONLY);
    if (fd < 0) return 0;
    size_t total = 0;
    for (;;) {
        ssize_t r = read(fd, maps_buf + total, sizeof maps_buf - total - 1);
        if (r <= 0) break;
        total += (size_t)r;
        if (total >= sizeof maps_buf - 1) break;
    }
    close(fd);
    maps_buf[total] = '\0';
    return total;
}

static void parse_maps(size_t len) {
    size_t i = 0;
    region_count = 0;
    while (i < len && region_count < MAX_REGION) {
        size_t line_start = i;
        while (i < len && maps_buf[i] != '\n') i++;
        size_t line_len = i - line_start;
        if (i < len) i++;
        if (line_len == 0) continue;

        char line[LINE_BUF];
        if (line_len >= sizeof line) continue;
        memcpy(line, maps_buf + line_start, line_len);
        line[line_len] = '\0';

        struct region *r = &regions[region_count];
        r->path[0] = '\0';
        char *dash = strchr(line, '-');
        if (!dash) continue;
        *dash = '\0';
        r->start = strtoul(line, NULL, 16);
        char *rest = dash + 1;
        char *sp = strchr(rest, ' ');
        if (!sp) continue;
        *sp = '\0';
        r->end = strtoul(rest, NULL, 16);
        rest = sp + 1;
        sp = strchr(rest, ' ');
        if (!sp) continue;
        *sp = '\0';
        strncpy(r->perms, rest, sizeof r->perms - 1);
        r->perms[sizeof r->perms - 1] = '\0';
        /* pathname is the last field; it may be absent (anonymous). */
        char *p = sp + 1;
        char *last = strrchr(p, ' ');
        if (last) {
            last++;
            while (*last == ' ') last++;
            if (*last && *last != '\n') {
                strncpy(r->path, last, sizeof r->path - 1);
                r->path[sizeof r->path - 1] = '\0';
            }
        }
        region_count++;
    }
}

/* ---- the domain rule ---- */

enum verdict {
    HEAP = 0,
    EXC_FILE,      /* file-backed: code, rodata, data, any mapped object */
    EXC_EXEC,      /* executable: JIT code cache, patched/injected code */
    EXC_NOREAD,    /* PROT_NONE: guard pages, uncommitted arena reserve */
    EXC_STACK,     /* [stack] / [tstack:N]: a separate hashed domain */
    EXC_SPECIAL,   /* [vdso] [vvar] [vsyscall]: kernel-owned */
    EXC_BSS,       /* anonymous, but adjacent to its object's file mapping */
    EXC_TAGGED,    /* [anon:NAME]: the allocator NAMED its own arena, so the
                    * kernel itself tells us this memory is not the guest's */
    VERDICT_MAX
};
static const char *verdict_name[VERDICT_MAX] = {
    "heap", "file", "exec", "noread", "stack", "special", "bss", "tagged"
};

static int is_special_name(const char *p) {
    return strcmp(p, "[vdso]") == 0 || strcmp(p, "[vvar]") == 0 ||
           strcmp(p, "[vsyscall]") == 0 || strcmp(p, "[vvar_vclock]") == 0;
}
static int is_stack_name(const char *p) {
    return strcmp(p, "[stack]") == 0 || strncmp(p, "[tstack", 7) == 0;
}

static enum verdict classify(int idx) {
    struct region *r = &regions[idx];
    const char *p = r->path;

    /* clause 5: kernel-special */
    if (p[0] == '[' && is_special_name(p)) return EXC_SPECIAL;
    /* clause 4: the stack domain is hashed separately, not part of the heap */
    if (p[0] == '[' && is_stack_name(p))   return EXC_STACK;
    /* clause 1: anonymous. [heap] is the brk segment -- anonymous and
     * guest-allocated, so it STAYS. Any other bracket name is not ours.
     *
     * `[anon:NAME]` is its own class and NOT a special case: it is an anonymous
     * VMA the allocator NAMED via PR_SET_VMA_ANON_NAME. That is a first-class,
     * kernel-visible provenance tag -- the runtime declaring "this arena is
     * mine, not the application's" -- so it is a domain clause, not an
     * exception list. SaBRe's mimalloc tags every arena this way; DynamoRIO
     * tags nothing, which is precisely why the two backends behave so
     * differently under this rule. */
    if (p[0] != '\0' && strcmp(p, "[heap]") != 0) {
        if (strncmp(p, "[anon:", 6) == 0) return EXC_TAGGED;
        if (p[0] == '[') return EXC_SPECIAL;
        return EXC_FILE;
    }
    /* clause 2: not executable -- this is the clause that makes the owner's
     * principle work. Patch bytes and JIT code live in executable regions, so
     * they were NEVER in the domain; nothing is excluded after the fact. */
    if (strchr(r->perms, 'x')) return EXC_EXEC;
    /* clause 3: readable */
    if (!strchr(r->perms, 'r')) return EXC_NOREAD;
    /* .bss: anonymous, but immediately following a file-backed mapping of an
     * object -- the "static region" the owner excludes. Derived per-run from
     * adjacency, so it stays a domain rule rather than an exception list. */
    if (strcmp(p, "[heap]") != 0 && idx > 0) {
        struct region *prev = &regions[idx - 1];
        if (prev->end == r->start && prev->path[0] == '/') return EXC_BSS;
    }
    return HEAP;
}

/* ---- deterministic allocation workload ---- */

static void *big[8];
static void *small_blocks[2000];

static void *thread_body(void *arg) {
    (void)arg;
    /* forces a second malloc arena on glibc */
    void *p = malloc(256 * 1024);
    if (p) memset(p, 0x5a, 256 * 1024);
    return p;
}

static void allocate(int mutate, int threads) {
    /* small: served from brk, i.e. the kernel's [heap] */
    for (int i = 0; i < 2000; i++) {
        small_blocks[i] = malloc(64);
        if (small_blocks[i]) memset(small_blocks[i], (unsigned char)(i & 0xff), 64);
    }
    /* large: above glibc's 128 KiB M_MMAP_THRESHOLD, so anonymous mmap and
     * INVISIBLE to a [heap]-only rule -- the 0.2%-domain gap in the design doc */
    for (int i = 0; i < 8; i++) {
        big[i] = malloc(4 * 1024 * 1024);
        if (big[i]) memset(big[i], (unsigned char)(0xA0 + i), 4 * 1024 * 1024);
    }
    if (threads > 0) {
        pthread_t t;
        if (pthread_create(&t, NULL, thread_body, NULL) == 0) pthread_join(t, NULL);
    }
    /* The planted negative: one byte, deep inside one large allocation. If the
     * instrument reports EQUAL with this on, the domain is empty or the digest
     * is not reading guest data -- a "match" would be vacuous. */
    if (mutate && big[3]) ((unsigned char *)big[3])[1234567] ^= 0xff;
}

int main(int argc, char **argv) {
    int mutate = 0, threads = 0, quiet = 0, verbose_map = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--mutate") == 0) mutate = 1;
        else if (strcmp(argv[i], "--threads") == 0 && i + 1 < argc) threads = atoi(argv[++i]);
        else if (strcmp(argv[i], "--quiet") == 0) quiet = 1;
        /* print EVERY region with the verdict the rule assigned it, so an
         * exclusion can be attributed to a clause rather than inferred */
        else if (strcmp(argv[i], "--verbose-map") == 0) verbose_map = 1;
    }

    allocate(mutate, threads);

    /* Snapshot AFTER allocating and BEFORE hashing. Nothing below allocates. */
    size_t len = read_maps();
    if (len == 0) {
        emit_str("PROBE_ERROR no /proc/self/maps -- NO RESULT, not a match\n");
        return 2;
    }
    parse_maps(len);
    if (region_count == 0) {
        emit_str("PROBE_ERROR parsed zero regions -- NO RESULT, not a match\n");
        return 2;
    }

    unsigned long excl_count[VERDICT_MAX] = {0};
    unsigned long excl_bytes[VERDICT_MAX] = {0};
    unsigned long heap_bytes = 0, heap_regions = 0;
    uint64_t rolling = 1469598103934665603ULL;

    for (int i = 0; i < region_count; i++) {
        enum verdict v = classify(i);
        unsigned long size = regions[i].end - regions[i].start;
        excl_count[v]++;
        excl_bytes[v] += size;

        if (verbose_map) {
            char *o = out_buf;
            memcpy(o, "MAP 0x", 6); o += 6;
            o += put_hex(o, regions[i].start, 1);
            memcpy(o, "-0x", 3); o += 3;
            o += put_hex(o, regions[i].end, 1);
            *o++ = ' ';
            size_t pl = strlen(regions[i].perms);
            memcpy(o, regions[i].perms, pl); o += pl;
            memcpy(o, " size=", 6); o += 6;
            o += put_dec(o, size);
            memcpy(o, " verdict=", 9); o += 9;
            size_t vl = strlen(verdict_name[v]);
            memcpy(o, verdict_name[v], vl); o += vl;
            memcpy(o, " path=", 6); o += 6;
            const char *pp = regions[i].path[0] ? regions[i].path : "-";
            size_t ppl = strlen(pp);
            if (ppl > 120) ppl = 120;
            memcpy(o, pp, ppl); o += ppl;
            *o++ = '\n';
            emit(out_buf, (size_t)(o - out_buf));
        }

        if (v != HEAP) continue;

        heap_regions++;
        heap_bytes += size;
        uint64_t d = fnv1a((const unsigned char *)regions[i].start, size);
        /* fold into a whole-domain digest, order-sensitive */
        rolling ^= d;
        rolling *= 1099511628211ULL;

        if (!quiet) {
            char *o = out_buf;
            memcpy(o, "REGION 0x", 9); o += 9;
            o += put_hex(o, regions[i].start, 1);
            memcpy(o, "-0x", 3); o += 3;
            o += put_hex(o, regions[i].end, 1);
            *o++ = ' ';
            size_t pl = strlen(regions[i].perms);
            memcpy(o, regions[i].perms, pl); o += pl;
            memcpy(o, " size=", 6); o += 6;
            o += put_dec(o, size);
            memcpy(o, " digest=", 8); o += 8;
            o += put_hex(o, (unsigned long)d, 16);
            *o++ = '\n';
            emit(out_buf, (size_t)(o - out_buf));
        }
    }

    /* A summary that states what it verified, including what it removed. Counts,
     * never a list -- the counts prove the rule ran without ever becoming a
     * maintained per-backend exception table. */
    char *o = out_buf;
    memcpy(o, "SUMMARY rule=A regions=", 23); o += 23;
    o += put_dec(o, heap_regions);
    memcpy(o, " bytes=", 7); o += 7;
    o += put_dec(o, heap_bytes);
    memcpy(o, " domain_digest=", 15); o += 15;
    o += put_hex(o, (unsigned long)rolling, 16);
    memcpy(o, " maps_regions=", 14); o += 14;
    o += put_dec(o, (unsigned long)region_count);
    *o++ = '\n';
    emit(out_buf, (size_t)(o - out_buf));

    o = out_buf;
    memcpy(o, "EXCLUDED", 8); o += 8;
    for (int v = 1; v < VERDICT_MAX; v++) {
        *o++ = ' ';
        size_t nl = strlen(verdict_name[v]);
        memcpy(o, verdict_name[v], nl); o += nl;
        *o++ = '=';
        o += put_dec(o, excl_count[v]);
        *o++ = '/';
        o += put_dec(o, excl_bytes[v]);
    }
    *o++ = '\n';
    emit(out_buf, (size_t)(o - out_buf));

    /* region_count==0 was refused above; a zero-region domain is a NO-RESULT. */
    return heap_regions == 0 ? 3 : 0;
}
