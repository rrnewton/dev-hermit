#!/usr/bin/env bash
cd /home/newton/work/dev-hermit
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib
export PYTHONDONTWRITEBYTECODE=1      # <-- what 05-qemu-boot.py sets, and my harness omitted
H=ignored/det4-parity/hermit/target/release/hermit
for t in qA qB; do
  W=ignored/det4-d5-$t; rm -rf $W; mkdir -p $W
  cp ignored/qemu-linux/hermit-boot.qcow2 $W/disk.qcow2
  timeout 900 $H --log info --log-file $PWD/$W/info.log run --backend=ptrace --strict \
    --target-timeslice 100000 --max-timeslice 2000000000 -- \
    python3 $PWD/demos/lib/qemu_controller.py boot --qemu $(command -v qemu-system-x86_64) \
    --qmp-socket $PWD/$W/qmp.sock --serial-log $PWD/$W/serial.log --disk $PWD/$W/disk.qcow2 \
    --kernel $PWD/ignored/qemu-linux/bzImage --initrd $PWD/ignored/qemu-linux/initramfs.cpio.gz \
    --snapshot-name $t --timeout 600 > $W/out.log 2> $W/err.log
  echo "$t rc=$? records=$(grep -cE 'DETLOG|COMMIT turn' $W/info.log 2>/dev/null||echo 0)"
done
