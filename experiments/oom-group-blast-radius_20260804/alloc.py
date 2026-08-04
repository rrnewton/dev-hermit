import sys, time
# Bounded over-cap allocator: grab `mb` MiB in 4 MiB touched chunks, then hold.
mb = int(sys.argv[1])
tag = sys.argv[2] if len(sys.argv) > 2 else "?"
chunks = []
grabbed = 0
while grabbed < mb:
    b = bytearray(4 * 1024 * 1024)
    for i in range(0, len(b), 4096):
        b[i] = 1
    chunks.append(b)
    grabbed += 4
    sys.stderr.write(f"[alloc {tag}] {grabbed} MiB\n"); sys.stderr.flush()
    time.sleep(0.01)
sys.stderr.write(f"[alloc {tag}] DONE holding {grabbed} MiB\n"); sys.stderr.flush()
time.sleep(600)
