import sys
# Allocate & TOUCH memory in 64MiB chunks up to target bytes, forcing RSS.
target = int(sys.argv[1])
chunk = 64 * 1024 * 1024
blocks = []
done = 0
while done < target:
    n = min(chunk, target - done)
    b = bytearray(n)
    for i in range(0, n, 4096):   # touch every page
        b[i] = 1
    blocks.append(b)
    done += n
    print(f"touched {done//(1024*1024)}MiB", flush=True)
print(f"REACHED-TARGET {done//(1024*1024)}MiB", flush=True)
