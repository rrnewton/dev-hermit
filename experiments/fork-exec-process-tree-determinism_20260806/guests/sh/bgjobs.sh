#!/bin/sh
# Background jobs reaped by `wait`: the shell's job table drives the reap order.
/bin/echo one &
/bin/echo two &
/bin/echo three &
wait
/bin/echo after-wait
