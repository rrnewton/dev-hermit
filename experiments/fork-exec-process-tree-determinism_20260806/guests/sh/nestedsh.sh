#!/bin/sh
# Nested shells: depth-3 process tree, each level forking and waiting.
/bin/sh -c '/bin/sh -c "/bin/sh -c \"/bin/echo deep\"; /bin/echo mid2"; /bin/echo mid1'
/bin/echo top
