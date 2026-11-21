#include "RootManagementClient.h"

#if defined(MINIS_ROOT_MANAGEMENT) && MINIS_ROOT_MANAGEMENT
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define OM_ROOT_OUTPUT_CAPACITY 4096
#define OM_ROOT_TIMEOUT_MS 6000

typedef int (*persona_fn)(const posix_spawnattr_t *, uid_t, uint32_t);
typedef int (*persona_id_fn)(const posix_spawnattr_t *, uid_t);

int OMRootManagementClientVersion(void) { return 1; }

static const char *operation_name(OMRootOperation operation) {
    switch (operation) {
        case OMRootOperationIdentity: return "identity";
        case OMRootOperationPrepare: return "prepare";
        case OMRootOperationCheck: return "check";
        case OMRootOperationCleanup: return "cleanup";
    }
    return NULL;
}

static int64_t monotonic_ms(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value) != 0) return -1;
    return (int64_t)value.tv_sec * 1000 + value.tv_nsec / 1000000;
}

static int roothide_bundle_matches(const char *bundle) {
    /* RootHide's dpkg creates .jbroot beside each Mach-O. Its public jbroot
     * API maps the fixed logical install path to this boot's randomized root.
     * Load it only for the RootHide fallback; other installations need no
     * additional library, and no caller can supply a path to this function. */
    const char suffix[] = "/.jbroot/usr/lib/libroothide.dylib";
    char library[PATH_MAX];
    size_t length = strlen(bundle);
    if (length + sizeof(suffix) > sizeof(library)) return 0;
    memcpy(library, bundle, length);
    memcpy(library + length, suffix, sizeof(suffix));
    void *handle = dlopen(library, RTLD_NOW | RTLD_LOCAL);
    if (!handle) return 0;
    typedef const char *(*jbroot_fn)(const char *);
    jbroot_fn resolve_root = (jbroot_fn)dlsym(handle, "jbroot");
    const char *installed = resolve_root ? resolve_root("/Applications/MinisRoot.app") : NULL;
    char canonical[PATH_MAX];
    int matches = installed && realpath(installed, canonical) && strcmp(bundle, canonical) == 0;
    /* jbroot returns library-owned cached memory. Finish comparing before
     * releasing the library; never free or retain the returned pointer. */
    dlclose(handle);
    return matches;
}

static int helper_path(char path[PATH_MAX]) {
    char executable[PATH_MAX];
    uint32_t capacity = sizeof(executable);
    if (_NSGetExecutablePath(executable, &capacity) != 0) return ENAMETOOLONG;
    if (!realpath(executable, path)) return errno;
    char *slash = strrchr(path, '/');
    if (!slash) return EINVAL;
    *slash = '\0';
    const char *bundle = strrchr(path, '/');
    if (!bundle) return EINVAL;
    if (strcmp(bundle + 1, "Minis.app") != 0) {
        /* /var/jb may resolve into preboot. Compare the two canonical bundle
         * directories, but leave the helper itself unresolved for lstat. */
        static const char *const installed_bundles[] = {
            "/Applications/MinisRoot.app",
            "/var/jb/Applications/MinisRoot.app",
            "/private/var/jb/Applications/MinisRoot.app",
        };
        char installed_bundle[PATH_MAX];
        int matched = 0;
        const size_t count = sizeof(installed_bundles) / sizeof(installed_bundles[0]);
        for (size_t index = 0; index < count; ++index) {
            if (realpath(installed_bundles[index], installed_bundle) &&
                strcmp(path, installed_bundle) == 0) {
                matched = 1;
                break;
            }
        }
        if (!matched && !roothide_bundle_matches(path)) return EINVAL;
    }
    const char suffix[] = "/minis-rootprobe";
    if (strlen(path) + sizeof(suffix) > PATH_MAX) return ENAMETOOLONG;
    strcat(path, suffix);
    struct stat st;
    if (lstat(path, &st) != 0) return errno;
    if (!S_ISREG(st.st_mode) || st.st_nlink != 1 || !(st.st_mode & 0111) ||
        (st.st_mode & 0022)) return EPERM;
    return 0;
}

static int set_cloexec(int fd) {
    int flags = fcntl(fd, F_GETFD);
    if (flags < 0 || fcntl(fd, F_SETFD, flags | FD_CLOEXEC) != 0) return errno;
    return 0;
}

/* The helper arms its own alarm before any operation. Reap asynchronously only
 * in the exceptional case where a child has not exited at the client deadline;
 * never let an unkillable child keep the caller's worker blocked forever. */
static void *reap_child(void *context) {
    pid_t child = *(pid_t *)context;
    free(context);
    int status;
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
    return NULL;
}

static void terminate_and_reap(pid_t child, OMRootManagementResult *result) {
    if (kill(child, SIGKILL) != 0 && errno != ESRCH && result->error_number == 0)
        result->error_number = errno;
    int status;
    pid_t waited;
    do { waited = waitpid(child, &status, WNOHANG); } while (waited < 0 && errno == EINTR);
    if (waited == child) {
        if (WIFEXITED(status)) result->exit_code = WEXITSTATUS(status);
        if (WIFSIGNALED(status)) result->termination_signal = WTERMSIG(status);
        return;
    }
    if (waited < 0) return;
    pid_t *context = malloc(sizeof(*context));
    if (!context) return;
    *context = child;
    pthread_t reaper;
    int error = pthread_create(&reaper, NULL, reap_child, context);
    if (error == 0) (void)pthread_detach(reaper);
    else { free(context); if (result->error_number == 0) result->error_number = error; }
}

OMRootManagementResult OMRootManagementRun(OMRootOperation operation,
                                          char *output, size_t output_capacity) {
    OMRootManagementResult result = {0};
    result.exit_code = -1;
    result.caller_uid = getuid();
    result.caller_euid = geteuid();
    result.stage = "client.arguments";
    const char *name = operation_name(operation);
    if (!output || output_capacity < 2 || !name) { result.error_number = EINVAL; return result; }
    if (output_capacity > OM_ROOT_OUTPUT_CAPACITY) output_capacity = OM_ROOT_OUTPUT_CAPACITY;
    output[0] = '\0';
    result.stage = "client.thread";
    if (pthread_main_np()) { result.error_number = EDEADLK; return result; }

    char path[PATH_MAX];
    result.stage = "client.helper-path";
    result.error_number = helper_path(path);
    if (result.error_number != 0) return result;

    /* These are deliberately resolved at runtime: missing persona support is
     * an observed error, never an unresolved private symbol at app launch. */
    result.stage = "client.persona-symbols";
    persona_fn set_persona = (persona_fn)dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_np");
    persona_id_fn set_uid = (persona_id_fn)dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_uid_np");
    persona_id_fn set_gid = (persona_id_fn)dlsym(RTLD_DEFAULT, "posix_spawnattr_set_persona_gid_np");
    if (!set_persona || !set_uid || !set_gid) { result.error_number = ENOSYS; return result; }

    posix_spawnattr_t attributes;
    posix_spawn_file_actions_t actions;
    result.stage = "client.spawn-attributes";
    int error = posix_spawnattr_init(&attributes);
    if (error != 0) { result.error_number = error; return result; }
#ifdef POSIX_SPAWN_CLOEXEC_DEFAULT
    error = posix_spawnattr_setflags(&attributes, POSIX_SPAWN_CLOEXEC_DEFAULT);
#else
    error = ENOTSUP;
#endif
    if (error == 0) error = set_persona(&attributes, 99, 1);
    if (error == 0) error = set_uid(&attributes, 0);
    if (error == 0) error = set_gid(&attributes, 0);
    if (error != 0) {
        (void)posix_spawnattr_destroy(&attributes);
        result.error_number = error; return result;
    }

    result.stage = "client.pipe";
    int pipefd[2];
    if (pipe(pipefd) != 0) {
        result.error_number = errno; (void)posix_spawnattr_destroy(&attributes); return result;
    }
    /* Reserve descriptors above stdio so spawn actions cannot close a newly
     * opened /dev/null or stdout when an inherited stdio descriptor is absent. */
    for (int i = 0; i < 2; ++i) {
        if (pipefd[i] < 3) {
            int replacement = fcntl(pipefd[i], F_DUPFD_CLOEXEC, 3);
            if (replacement < 0) { error = errno; break; }
            close(pipefd[i]); pipefd[i] = replacement;
        }
        error = set_cloexec(pipefd[i]);
        if (error != 0) break;
    }
    int read_flags = error == 0 ? fcntl(pipefd[0], F_GETFL) : -1;
    if (error == 0 && (read_flags < 0 || fcntl(pipefd[0], F_SETFL, read_flags | O_NONBLOCK) != 0))
        error = errno;
    if (error != 0) {
        close(pipefd[0]); close(pipefd[1]); (void)posix_spawnattr_destroy(&attributes);
        result.error_number = error; return result;
    }
    result.stage = "client.file-actions";
    error = posix_spawn_file_actions_init(&actions);
    if (error != 0) {
        close(pipefd[0]); close(pipefd[1]); (void)posix_spawnattr_destroy(&attributes);
        result.error_number = error; return result;
    }
    error = posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0);
    if (error == 0) error = posix_spawn_file_actions_addopen(&actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0);
    if (error == 0) error = posix_spawn_file_actions_adddup2(&actions, pipefd[1], STDOUT_FILENO);
    if (error == 0) error = posix_spawn_file_actions_addclose(&actions, pipefd[0]);
    if (error == 0) error = posix_spawn_file_actions_addclose(&actions, pipefd[1]);
    int64_t started = monotonic_ms();
    if (error == 0 && started < 0) error = errno ? errno : EIO;
    char *const argv[] = {path, (char *)name, NULL};
    char *const environment[] = {"PATH=/usr/bin:/bin", "LANG=C", NULL};
    pid_t child = -1;
    if (error == 0) {
        result.stage = "client.spawn";
        error = posix_spawn(&child, path, &actions, &attributes, argv, environment);
    }
    (void)posix_spawn_file_actions_destroy(&actions);
    (void)posix_spawnattr_destroy(&attributes);
    close(pipefd[1]);
    if (error != 0) { close(pipefd[0]); result.error_number = error; return result; }

    int exited = 0, eof = 0;
    while (!exited || !eof) {
        result.stage = "client.read";
        char buffer[256];
        for (;;) {
            ssize_t count = read(pipefd[0], buffer, sizeof(buffer));
            if (count == 0) { eof = 1; break; }
            if (count < 0 && errno == EINTR) continue;
            if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
            if (count < 0) { result.error_number = errno; break; }
            size_t remaining = output_capacity - 1 - result.output_bytes;
            size_t copied = (size_t)count < remaining ? (size_t)count : remaining;
            memcpy(output + result.output_bytes, buffer, copied);
            result.output_bytes += copied;
            output[result.output_bytes] = '\0';
            if (copied != (size_t)count) {
                result.output_truncated = 1; result.error_number = EOVERFLOW; break;
            }
        }
        if (result.error_number != 0) break;
        if (!exited) {
            int status;
            pid_t waited = waitpid(child, &status, WNOHANG);
            if (waited == child) {
                exited = 1;
                if (WIFEXITED(status)) result.exit_code = WEXITSTATUS(status);
                if (WIFSIGNALED(status)) result.termination_signal = WTERMSIG(status);
            } else if (waited < 0 && errno != EINTR) {
                result.stage = "client.wait"; result.error_number = errno; break;
            }
        }
        if (exited && eof) break;
        int64_t now = monotonic_ms();
        if (now < 0 || now - started >= OM_ROOT_TIMEOUT_MS) {
            result.stage = "client.timeout";
            result.timed_out = 1; result.error_number = now < 0 ? EIO : ETIMEDOUT; break;
        }
        struct pollfd descriptor = {pipefd[0], POLLIN | POLLHUP, 0};
        if (poll(&descriptor, 1, 20) < 0 && errno != EINTR) {
            result.stage = "client.poll"; result.error_number = errno; break;
        }
    }
    close(pipefd[0]);
    if (!exited) terminate_and_reap(child, &result);
    if (result.error_number == 0) result.stage = "client.completed";
    return result;
}
#endif
