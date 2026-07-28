#ifndef SHMEM_POD_BOOTSTRAP_H
#define SHMEM_POD_BOOTSTRAP_H

#include <stdint.h>

#define SHMEM_POD_BOOTSTRAP_ABI_VERSION UINT16_C(1)
#define SHMEM_POD_BOOTSTRAP_ENCODED_LEN 160

enum shmem_pod_connector_kind {
    SHMEM_POD_CONNECTOR_COOPERATIVE = 1,
    SHMEM_POD_CONNECTOR_PRELOAD = 2,
    SHMEM_POD_CONNECTOR_PTRACE = 3,
    SHMEM_POD_CONNECTOR_TRAMPOLINE = 4,
};

enum shmem_pod_bootstrap_flags {
    SHMEM_POD_BOOTSTRAP_REQUIRED = 1u << 0,
    SHMEM_POD_BOOTSTRAP_INHERIT_ACROSS_EXEC = 1u << 1,
    SHMEM_POD_BOOTSTRAP_FIXED_CODE_ADDRESS = 1u << 2,
    SHMEM_POD_BOOTSTRAP_FIXED_STATE_ADDRESS = 1u << 3,
    /* Caller-verified provenance assertion; the context cannot prove receipt. */
    SHMEM_POD_BOOTSTRAP_SCM_RIGHTS_TRANSPORT = 1u << 4,
};

struct shmem_pod_bootstrap_v1 {
    uint8_t magic[8];
    uint16_t abi_version;
    uint16_t struct_size;
    uint32_t flags;
    uint32_t connector;
    uint32_t reserved_word;
    int32_t artifact_fd;
    int32_t code_fd;
    int32_t state_fd;
    int32_t control_fd;
    uint64_t artifact_len;
    uint64_t state_len;
    uint64_t code_address;
    uint64_t state_address;
    uint64_t generation;
    uint8_t api_fingerprint[16];
    uint8_t artifact_sha256[32];
    uint8_t instance_nonce[16];
    uint8_t reserved[16];
};

typedef int32_t (*shmem_pod_bootstrap_entry_v1)(
    const struct shmem_pod_bootstrap_v1 *context);

_Static_assert(sizeof(struct shmem_pod_bootstrap_v1) ==
                   SHMEM_POD_BOOTSTRAP_ENCODED_LEN,
               "bootstrap ABI size mismatch");

#endif
