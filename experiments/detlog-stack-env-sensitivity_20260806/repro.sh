#!/bin/bash
# Is --detlog-stack reproducible run-to-run on the golden ptrace reference?
# Answer depends entirely on whether the ENVIRONMENT is pinned.
set -u
H=${HERMIT_BIN:-/home/newton/work/dev-hermit/hermit/target/debug/hermit}
LU=/home/newton/work/dev-hermit/ignored/lu-parity/usr/lib64
G=${GUEST:?set GUEST to a small binary OUTSIDE /tmp (hermit isolates guest /tmp)}
dl() { sed -n 's/.*\(DETLOG .*\)/\1/p'; }
ARGS="-l info run --backend ptrace --strict --detlog-heap --detlog-stack --"

echo "== A: ambient environment (inherited), two runs =="
LD_LIBRARY_PATH=$LU $H $ARGS "$G" 2>&1 | dl > a1.txt
LD_LIBRARY_PATH=$LU $H $ARGS "$G" 2>&1 | dl > a2.txt

echo "== B: PINNED environment (env -i), two runs =="
env -i PATH=/usr/bin:/bin HOME="$HOME" LD_LIBRARY_PATH=$LU $H $ARGS "$G" 2>&1 | dl > b1.txt
env -i PATH=/usr/bin:/bin HOME="$HOME" LD_LIBRARY_PATH=$LU $H $ARGS "$G" 2>&1 | dl > b2.txt

echo "== C: env PERTURBED by one variable of different length =="
env -i PATH=/usr/bin:/bin HOME="$HOME" LD_LIBRARY_PATH=$LU X=short $H $ARGS "$G" 2>&1 | dl > c1.txt
env -i PATH=/usr/bin:/bin HOME="$HOME" LD_LIBRARY_PATH=$LU X=a_much_longer_value $H $ARGS "$G" 2>&1 | dl > c2.txt

python3 - <<'PY'
def rd(p): return [l.rstrip("\n") for l in open(p)]
for label,x,y in (("ambient","a1.txt","a2.txt"),("pinned","b1.txt","b2.txt"),("perturbed","c1.txt","c2.txt")):
    A,B=rd(x),rd(y)
    d=[(p,q) for p,q in zip(A,B) if p!=q]
    st=sum(1 for p,_ in d if '[stack]' in p); hp=sum(1 for p,_ in d if '[heap]' in p)
    print(f"{label:8s} lines={len(A):4d} differing={len(d):4d}  stack={st} heap={hp} other={len(d)-st-hp}")
PY
