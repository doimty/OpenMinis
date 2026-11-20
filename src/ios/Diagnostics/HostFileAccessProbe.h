#ifndef MINIS_HOST_FILE_ACCESS_PROBE_H
#define MINIS_HOST_FILE_ACCESS_PROBE_H

#include <stdint.h>

enum {
    OMHostProbeMatch = 0,
    OMHostProbeSystemError,
    OMHostProbeNotRegular,
    OMHostProbeUnexpectedSize,
    OMHostProbeInvalidMarker,
    OMHostProbeFileChanged,
    OMHostProbeMultipleLinks,
};

typedef struct {
    int outcome;
    int error_number;         /* Saved errno, or zero for a probe-side rejection. */
    const char *stage;        /* Static label; never a user-provided string. */
    const char *sample_id;    /* Canned public ID on an exact match; NULL otherwise. */
    uint64_t device;
    uint64_t inode;
    uint64_t size;
    int64_t mtime_seconds;
    int64_t mtime_nanoseconds;
} OMHostFileProbeResult;

/* Definitions exist only in builds explicitly opting into this diagnostic. */
const char *OMHostFileProbeSamplePath(void);
OMHostFileProbeResult OMHostFileProbeReadSample(void);

#endif
