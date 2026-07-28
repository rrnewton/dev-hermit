#include "shmem_pod_bootstrap.h"

#include <stddef.h>

_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, artifact_fd) == 24,
               "artifact fd offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, generation) == 72,
               "generation offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, artifact_sha256) == 96,
               "artifact digest offset mismatch");

int main(void) { return 0; }
