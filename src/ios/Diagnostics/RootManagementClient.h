#ifndef MINIS_ROOT_MANAGEMENT_CLIENT_H
#define MINIS_ROOT_MANAGEMENT_CLIENT_H

#if defined(MINIS_ROOT_MANAGEMENT) && MINIS_ROOT_MANAGEMENT
#include <stddef.h>
#include <stdint.h>

typedef enum {
    OMRootOperationIdentity = 0,
    OMRootOperationPrepare = 1,
    OMRootOperationCheck = 2,
    OMRootOperationCleanup = 3,
} OMRootOperation;

typedef struct {
    int error_number;
    int exit_code;           /* -1 unless a child exited normally. */
    int termination_signal;
    int timed_out;
    int output_truncated;
    size_t output_bytes;
    uint32_t caller_uid;
    uint32_t caller_euid;
    const char *stage;       /* A fixed client label, never child output. */
} OMRootManagementResult;

/* Exists in root-enabled binaries only; the packager checks the linked symbol. */
/* Release compiles this target with -fvisibility=hidden, so the sentinel opts
 * back into default visibility to stay externally visible in the product. */
__attribute__((visibility("default"))) int OMRootManagementClientVersion(void);

/*
 * Call on a worker thread. Only the four enum values above can be executed.
 * The executable is always this app bundle's minis-rootprobe; no caller can
 * supply a path, command, environment or extra argument. stdout is bounded to
 * 4095 bytes. It is untrusted protocol data until the Swift validator accepts
 * its schema, source commit and scalar fields.
 */
OMRootManagementResult OMRootManagementRun(OMRootOperation operation,
                                          char *output, size_t output_capacity);
#endif
#endif
