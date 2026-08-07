#!/usr/bin/env bash
# demo05 golden self-determinism with ALL harness variables controlled:
#   - PYTHONDONTWRITEBYTECODE=1  (what 05-qemu-boot.py sets; shared .pyc otherwise varies)
#   - IDENTICAL working directory and argv for both runs (paths appear in guest execve argv)
cd /home/newton/work/dev-hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
export PYTHONDONTWRITEBYTECODE=1
H=ignored/det4-parity/hermit/target/release/hermit
W=ignored/det4-d5-ctl; rm -rf $W; mkdir -p $W
for t in 1 2; do
  cp ignored/qemu-linux/hermit-boot.qcow2 $W/disk.qcow2
  rm -f $W/qmp.sock
  timeout 900 $H --log info --log-file $PWD/$W/info.log run --backend=ptrace --strict \
    --target-timeslice 100000 --max-timeslice 2000000000 -- \
    python3 $PWD/demos/lib/qemu_controller.py boot --qemu $(command -v qemu-system-x86_64) \
    --qmp-socket $PWD/$W/qmp.sock --serial-log $PWD/$W/serial.log --disk $PWD/$W/disk.qcow2 \
    --kernel $PWD/ignored/qemu-linux/bzImage --initrd $PWD/ignored/qemu-linux/initramfs.cpio.gz \
    --snapshot-name ctl --timeout 600 > $W/out.log 2> $W/err.log
  echo "run$t rc=$? records=$(grep -cE 'DETLOG|COMMIT turn' $W/info.log 2>/dev/null||echo 0)"
  mv $W/info.log $W/r$t.log
done
