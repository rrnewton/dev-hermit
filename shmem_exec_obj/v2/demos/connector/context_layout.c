#include "shmem_pod_bootstrap.h"

#include <stddef.h>

_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, magic) == 0,
               "magic offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, abi_version) == 8,
               "ABI version offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, struct_size) == 10,
               "struct size offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, flags) == 12,
               "flags offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, connector) == 16,
               "connector offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, reserved_word) == 20,
               "reserved word offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, artifact_fd) == 24,
               "artifact fd offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, code_fd) == 28,
               "code fd offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, state_fd) == 32,
               "state fd offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, control_fd) == 36,
               "control fd offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, artifact_len) == 40,
               "artifact length offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, state_len) == 48,
               "state length offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, code_address) == 56,
               "code address offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, state_address) == 64,
               "state address offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, generation) == 72,
               "generation offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, api_fingerprint) == 80,
               "API fingerprint offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, artifact_sha256) == 96,
               "artifact digest offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, instance_nonce) == 128,
               "instance nonce offset mismatch");
_Static_assert(offsetof(struct shmem_pod_bootstrap_v1, reserved) == 144,
               "reserved bytes offset mismatch");
_Static_assert(_Alignof(struct shmem_pod_bootstrap_v1) == 8,
               "bootstrap ABI alignment mismatch");

int main(void) { return 0; }
