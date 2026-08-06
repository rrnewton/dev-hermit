# A real `make` subprocess tree: make forks+execs /bin/sh per recipe line.
all: a b c
a: ; @/bin/echo target-a
b: a ; @/bin/echo target-b
c: b ; @/bin/echo target-c
