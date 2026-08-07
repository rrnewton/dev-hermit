#!/bin/bash
# bash-builtin probes: $RANDOM and the procfs UUID (no PATH needed).
echo "bash_random=$RANDOM $RANDOM $RANDOM"
read -r u < /proc/sys/kernel/random/uuid
echo "procfs_uuid=$u"
