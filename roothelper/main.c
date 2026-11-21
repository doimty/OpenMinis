#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

// Keep the fixed-path no-symlink policy available when Darwin strict POSIX
// feature visibility hides this fcntl extension.
#if defined(__APPLE__) && !defined(O_NOFOLLOW)
#define O_NOFOLLOW 0x00000100
#endif

#define PROBE_DIRECTORY "OpenMinisRootProbe"
#define PROBE_FILENAME "sample-v1.txt"
#define PROBE_ID "fb81bca8-f8c0-4f2e-9ff2-fbfaf351aa87"
#ifndef MINIS_ROOT_HELPER_COMMIT
#define MINIS_ROOT_HELPER_COMMIT "unknown"
#endif
#define RESPONSE_METADATA "\"schema\":1,\"helper_commit\":\"" MINIS_ROOT_HELPER_COMMIT "\","
static const char payload[] = "OPENMINIS-ROOT-PROBE-V1:" PROBE_ID "\n";

static int open_parent(void) {
#ifdef MINIS_ROOT_PROBE_TEST_ROOT
    const char *root = getenv("MINIS_ROOT_PROBE_TEST_ROOT");
    if (!root || root[0] != '/') { errno = EINVAL; return -1; }
    int fd = open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
#else
    int fd = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
#endif
    static const char *const parts[] = {"private", "var", "mobile", "Library"};
    for (size_t i = 0; i < sizeof(parts) / sizeof(parts[0]); ++i) {
        int next = openat(fd, parts[i], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        int saved = errno;
        close(fd);
        if (next < 0) { errno = saved; return -1; }
        fd = next;
    }
    return fd;
}

static int valid_directory(int fd) {
    struct stat st;
    if (fstat(fd, &st) != 0) return 0;
    return S_ISDIR(st.st_mode) && st.st_uid == 0 && (st.st_mode & 0777) == 0700;
}

static int read_exact_probe(int dirfd, struct stat *identity) {
    struct stat before, after, named;
    int fd = openat(dirfd, PROBE_FILENAME, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    if (fstat(fd, &before) != 0) { int e = errno; close(fd); errno = e; return -1; }
    if (!S_ISREG(before.st_mode) || before.st_uid != 0 || (before.st_mode & 0777) != 0600 ||
        before.st_nlink != 1 || before.st_size != (off_t)(sizeof(payload) - 1)) {
        close(fd); errno = EPERM; return -1;
    }
    char bytes[sizeof(payload)];
    size_t got = 0;
    while (got < sizeof(bytes)) {
        ssize_t n = read(fd, bytes + got, sizeof(bytes) - got);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) { int e = errno; close(fd); errno = e; return -1; }
        if (n == 0) break;
        got += (size_t)n;
    }
    int ok = fstat(fd, &after) == 0 &&
             fstatat(dirfd, PROBE_FILENAME, &named, AT_SYMLINK_NOFOLLOW) == 0 &&
             before.st_dev == after.st_dev && before.st_ino == after.st_ino &&
             after.st_dev == named.st_dev && after.st_ino == named.st_ino &&
             after.st_size == before.st_size && got == sizeof(payload) - 1 &&
             memcmp(bytes, payload, sizeof(payload) - 1) == 0;
    int saved = errno;
    close(fd);
    if (!ok) { errno = saved ? saved : EINVAL; return -1; }
    if (identity) *identity = after;
    return 0;
}

static void emit_error(const char *operation, int error_number) {
    printf("{" RESPONSE_METADATA "\"ok\":false,\"operation\":\"%s\",\"uid\":%u,\"euid\":%u,\"errno\":%d}\n",
           operation, (unsigned)getuid(), (unsigned)geteuid(), error_number);
}

static int operation_prepare(int parent) {
    if (mkdirat(parent, PROBE_DIRECTORY, 0700) != 0 && errno != EEXIST) {
        emit_error("prepare", errno); return 1;
    }
    int dir = openat(parent, PROBE_DIRECTORY, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) { emit_error("prepare.open-directory", errno); return 1; }
    if (!valid_directory(dir)) { close(dir); emit_error("prepare.directory-policy", EPERM); return 1; }
    int fd = openat(dir, PROBE_FILENAME, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0 && errno == EEXIST) {
        if (read_exact_probe(dir, NULL) == 0) {
            printf("{" RESPONSE_METADATA "\"ok\":true,\"operation\":\"prepare\",\"already_prepared\":true,\"uid\":%u,\"euid\":%u,\"marker_id\":\"%s\"}\n",
                   (unsigned)getuid(), (unsigned)geteuid(), PROBE_ID);
            close(dir); return 0;
        }
        emit_error("prepare.existing-file", errno); close(dir); return 1;
    }
    if (fd < 0) { emit_error("prepare.create-file", errno); close(dir); return 1; }
    int failed = fchown(fd, 0, 0) != 0 || fchmod(fd, 0600) != 0;
    size_t written = 0;
    while (!failed && written < sizeof(payload) - 1) {
        ssize_t n = write(fd, payload + written, sizeof(payload) - 1 - written);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { failed = 1; break; }
        written += (size_t)n;
    }
    if (!failed && fsync(fd) != 0) failed = 1;
    int saved = errno;
    close(fd);
    if (failed) {
        unlinkat(dir, PROBE_FILENAME, 0);
        close(dir); emit_error("prepare.write", saved ? saved : EIO); return 1;
    }
    close(dir);
    printf("{" RESPONSE_METADATA "\"ok\":true,\"operation\":\"prepare\",\"uid\":%u,\"euid\":%u,\"marker_id\":\"%s\"}\n",
           (unsigned)getuid(), (unsigned)geteuid(), PROBE_ID);
    return 0;
}

static int operation_check(int parent) {
    int dir = openat(parent, PROBE_DIRECTORY, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) { emit_error("check.open-directory", errno); return 1; }
    if (!valid_directory(dir)) { close(dir); emit_error("check.directory-policy", EPERM); return 1; }
    struct stat st;
    if (read_exact_probe(dir, &st) != 0) { int e = errno; close(dir); emit_error("check.read", e); return 1; }
    close(dir);
    printf("{" RESPONSE_METADATA "\"ok\":true,\"operation\":\"check\",\"uid\":%u,\"euid\":%u,\"marker_id\":\"%s\",\"device\":\"%" PRIuMAX "\",\"inode\":\"%" PRIuMAX "\",\"bytes\":%" PRIuMAX "}\n",
           (unsigned)getuid(), (unsigned)geteuid(), PROBE_ID,
           (uintmax_t)st.st_dev, (uintmax_t)st.st_ino, (uintmax_t)st.st_size);
    return 0;
}

static int operation_cleanup(int parent) {
    int dir = openat(parent, PROBE_DIRECTORY, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) { emit_error("cleanup.open-directory", errno); return 1; }
    if (!valid_directory(dir)) { close(dir); emit_error("cleanup.directory-policy", EPERM); return 1; }
    if (read_exact_probe(dir, NULL) != 0) { int e = errno; close(dir); emit_error("cleanup.verify", e); return 1; }
    if (unlinkat(dir, PROBE_FILENAME, 0) != 0) { int e = errno; close(dir); emit_error("cleanup.unlink", e); return 1; }
    close(dir); /* Leave the empty, root-owned 0700 directory; never unlink a raced replacement. */
    printf("{" RESPONSE_METADATA "\"ok\":true,\"operation\":\"cleanup\",\"uid\":%u,\"euid\":%u,\"removed\":true}\n",
           (unsigned)getuid(), (unsigned)geteuid());
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2 || !argv[1]) { emit_error("invalid-arguments", EINVAL); return 64; }
    /* This path does not open the sample, create directories or change IDs.
     * It can report a failed persona transition without hiding the real EUID. */
    if (strcmp(argv[1], "identity") == 0) {
        int root = geteuid() == 0;
        printf("{" RESPONSE_METADATA "\"ok\":%s,\"operation\":\"identity\",\"uid\":%u,\"euid\":%u,\"errno\":%d}\n",
               root ? "true" : "false", (unsigned)getuid(), (unsigned)geteuid(), root ? 0 : EPERM);
        return root ? 0 : 77;
    }
    if (strcmp(argv[1], "prepare") != 0 && strcmp(argv[1], "check") != 0 && strcmp(argv[1], "cleanup") != 0) {
        emit_error("operation-not-allowed", EINVAL); return 64;
    }
    if (geteuid() != 0) { emit_error("not-root", EPERM); return 77; }
    if (setgid(0) != 0 || setuid(0) != 0) { emit_error("drop-real-ids", errno); return 77; }
    /* A mobile parent may not be able to signal its root child. The helper
     * therefore owns its deadline before touching the fixed sample. */
    sigset_t alarm_mask;
    if (signal(SIGALRM, SIG_DFL) == SIG_ERR || sigemptyset(&alarm_mask) != 0 ||
        sigaddset(&alarm_mask, SIGALRM) != 0 || sigprocmask(SIG_UNBLOCK, &alarm_mask, NULL) != 0) {
        emit_error("watchdog", errno); return 1;
    }
    alarm(4);
    const int parent = open_parent();
    if (parent < 0) { emit_error("open-parent", errno); return 1; }
    int rc;
    if (strcmp(argv[1], "prepare") == 0) rc = operation_prepare(parent);
    else if (strcmp(argv[1], "check") == 0) rc = operation_check(parent);
    else if (strcmp(argv[1], "cleanup") == 0) rc = operation_cleanup(parent);
    else { emit_error("operation-not-allowed", EINVAL); rc = 64; }
    close(parent);
    return rc;
}
