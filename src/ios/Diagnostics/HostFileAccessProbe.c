#include "HostFileAccessProbe.h"

#if defined(MINIS_HOST_FILE_PROBE) && MINIS_HOST_FILE_PROBE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#define PROBE_DIRECTORY "OpenMinisPermissionProbe"
#define PROBE_FILENAME "sample-v1.txt"
#define PROBE_SAMPLE_ID "20820eed-3d91-46bb-be1d-2d1b9058b29b"
static const char probe_contents[] = "OPENMINIS-PROBE-V1:" PROBE_SAMPLE_ID "\n";

const char *OMHostFileProbeSamplePath(void) {
#ifdef MINIS_HOST_FILE_PROBE_TEST_ROOT
    static char path[PATH_MAX];
    const char *root = getenv("MINIS_HOST_FILE_PROBE_TEST_ROOT");
    if (!root || root[0] != '/') return "<invalid test root>";
    (void)snprintf(path, sizeof(path), "%s/private/var/mobile/Media/%s/%s",
                   root, PROBE_DIRECTORY, PROBE_FILENAME);
    return path;
#else
    return "/private/var/mobile/Media/" PROBE_DIRECTORY "/" PROBE_FILENAME;
#endif
}

#if defined(__APPLE__) && defined(_POSIX_C_SOURCE) && !defined(_DARWIN_C_SOURCE)
// Darwin hides the BSD timespec members when strict POSIX feature visibility
// is requested, while retaining the seconds and nanoseconds as separate fields.
#define PROBE_MTIME_SECONDS(st) ((st)->st_mtime)
#define PROBE_MTIME_NANOSECONDS(st) ((st)->st_mtimensec)
#define PROBE_CTIME_SECONDS(st) ((st)->st_ctime)
#define PROBE_CTIME_NANOSECONDS(st) ((st)->st_ctimensec)
#elif defined(__APPLE__)
#define PROBE_MTIME_SECONDS(st) ((st)->st_mtimespec.tv_sec)
#define PROBE_MTIME_NANOSECONDS(st) ((st)->st_mtimespec.tv_nsec)
#define PROBE_CTIME_SECONDS(st) ((st)->st_ctimespec.tv_sec)
#define PROBE_CTIME_NANOSECONDS(st) ((st)->st_ctimespec.tv_nsec)
#else
#define PROBE_MTIME_SECONDS(st) ((st)->st_mtim.tv_sec)
#define PROBE_MTIME_NANOSECONDS(st) ((st)->st_mtim.tv_nsec)
#define PROBE_CTIME_SECONDS(st) ((st)->st_ctim.tv_sec)
#define PROBE_CTIME_NANOSECONDS(st) ((st)->st_ctim.tv_nsec)
#endif

static OMHostFileProbeResult report(int outcome, const char *stage, int error_number) {
    return (OMHostFileProbeResult){.outcome = outcome, .stage = stage, .error_number = error_number};
}

static int same_snapshot(const struct stat *a, const struct stat *b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino &&
           a->st_mode == b->st_mode && a->st_nlink == b->st_nlink && a->st_size == b->st_size &&
           PROBE_MTIME_SECONDS(a) == PROBE_MTIME_SECONDS(b) &&
           PROBE_MTIME_NANOSECONDS(a) == PROBE_MTIME_NANOSECONDS(b) &&
           PROBE_CTIME_SECONDS(a) == PROBE_CTIME_SECONDS(b) &&
           PROBE_CTIME_NANOSECONDS(a) == PROBE_CTIME_NANOSECONDS(b);
}

static OMHostFileProbeResult compare_sample(int directory, int file, const struct stat *expected) {
    struct stat before, after, named;
    if (fstat(file, &before) == -1) {
        return report(OMHostProbeSystemError, "sample.fstat-before", errno);
    }
    if (!same_snapshot(expected, &before)) {
        return report(OMHostProbeFileChanged, "sample.before-read", 0);
    }

    // The expected payload is sizeof(probe_contents) - 1. The extra byte lets
    // us detect unexpected trailing content without ever doing a larger read.
    char bytes[sizeof(probe_contents)];
    size_t total = 0;
    while (total < sizeof(bytes)) {
        ssize_t count = read(file, bytes + total, sizeof(bytes) - total);
        if (count == -1) {
            if (errno == EINTR) continue;
            return report(OMHostProbeSystemError, "sample.read", errno);
        }
        if (count == 0) break;
        total += (size_t)count;
    }

    if (fstat(file, &after) == -1) {
        return report(OMHostProbeSystemError, "sample.fstat-after", errno);
    }
    if (fstatat(directory, PROBE_FILENAME, &named, AT_SYMLINK_NOFOLLOW) == -1) {
        const int err = errno;
        return report(err == ENOENT ? OMHostProbeFileChanged : OMHostProbeSystemError,
                      "sample.recheck-name", err);
    }
    if (!same_snapshot(&before, &after) || !same_snapshot(&after, &named) ||
        total != sizeof(probe_contents) - 1) {
        return report(OMHostProbeFileChanged, "sample.after-read", 0);
    }
    if (memcmp(bytes, probe_contents, total) != 0) {
        return report(OMHostProbeInvalidMarker, "sample.compare", 0);
    }

    // Never return bytes from the file. Even the sample ID is a public literal.
    OMHostFileProbeResult result = report(OMHostProbeMatch, "sample.compare", 0);
    result.sample_id = PROBE_SAMPLE_ID;
    result.device = (uint64_t)after.st_dev;
    result.inode = (uint64_t)after.st_ino;
    result.size = (uint64_t)after.st_size;
    result.mtime_seconds = PROBE_MTIME_SECONDS(&after);
    result.mtime_nanoseconds = PROBE_MTIME_NANOSECONDS(&after);
    return result;
}

static OMHostFileProbeResult probe_sample_at(int directory) {
    struct stat expected;
    if (fstatat(directory, PROBE_FILENAME, &expected, AT_SYMLINK_NOFOLLOW) == -1) {
        return report(OMHostProbeSystemError, "sample.stat", errno);
    }
    if (!S_ISREG(expected.st_mode)) {
        return report(OMHostProbeNotRegular, "sample.stat", 0);
    }
    if (expected.st_nlink != 1) {
        return report(OMHostProbeMultipleLinks, "sample.stat", 0);
    }
    if (expected.st_size != (off_t)(sizeof(probe_contents) - 1)) {
        return report(OMHostProbeUnexpectedSize, "sample.stat", 0);
    }

    const int file = openat(directory, PROBE_FILENAME, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (file == -1) return report(OMHostProbeSystemError, "sample.open", errno);
    const OMHostFileProbeResult result = compare_sample(directory, file, &expected);
    close(file);
    return result;
}

static OMHostFileProbeResult probe_from_owned_root(int directory) {
    static const char *const components[] = {"private", "var", "mobile", "Media", PROBE_DIRECTORY};
    for (size_t i = 0; i < sizeof(components) / sizeof(components[0]); ++i) {
        const int next = openat(directory, components[i], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        const int err = errno;
        close(directory);
        if (next == -1) return report(OMHostProbeSystemError, components[i], err);
        directory = next;
    }
    const OMHostFileProbeResult result = probe_sample_at(directory);
    close(directory);
    return result;
}

OMHostFileProbeResult OMHostFileProbeReadSample(void) {
#ifdef MINIS_HOST_FILE_PROBE_TEST_ROOT
    const char *root = getenv("MINIS_HOST_FILE_PROBE_TEST_ROOT");
    if (!root || root[0] != '/') return report(OMHostProbeSystemError, "test-root", EINVAL);
#else
    const char *root = "/";
#endif
    const int directory = open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory == -1) return report(OMHostProbeSystemError, "root.open", errno);
    return probe_from_owned_root(directory);
}
#endif
