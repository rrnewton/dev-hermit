#define _GNU_SOURCE

#include "shmem_pod_bootstrap.h"

#include <dlfcn.h>
#include <stdio.h>

int main(int argc, char **argv) {
    void *handle;
    void *resident;
    shmem_pod_adapter_abi_version_entry_v1 version;

    if (argc != 2) {
        fprintf(stderr, "usage: %s SHIM.so\n", argv[0]);
        return 2;
    }
    handle = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        fprintf(stderr, "dlopen: %s\n", dlerror());
        return 2;
    }
    version = (shmem_pod_adapter_abi_version_entry_v1)dlsym(
        handle, "shmem_pod_adapter_abi_version_v1");
    if (version == NULL) {
        fprintf(stderr, "dlsym: %s\n", dlerror());
        return 2;
    }
    if (dlclose(handle) != 0) {
        fprintf(stderr, "dlclose: %s\n", dlerror());
        return 2;
    }
    if (version() != SHMEM_POD_BOOTSTRAP_ABI_VERSION) {
        fputs("adapter text was not callable after dlclose\n", stderr);
        return 2;
    }
    resident = dlopen(argv[1], RTLD_NOW | RTLD_NOLOAD);
    if (resident == NULL) {
        fputs("NODELETE adapter disappeared after dlclose\n", stderr);
        return 2;
    }
    dlclose(resident);
    puts("nodelete-ok callable_after_dlclose=true resident_after_dlclose=true");
    return 0;
}
