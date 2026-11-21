#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int root_helper_test_get_executable_path(char *buffer, uint32_t *capacity);
static char *root_helper_test_realpath(const char *source, char *resolved);
static void *root_helper_test_dlopen(const char *path, int flags);
static void *root_helper_test_dlsym(void *handle, const char *name);
static int root_helper_test_dlclose(void *handle);

#define _NSGetExecutablePath root_helper_test_get_executable_path
#define realpath root_helper_test_realpath
#define dlopen root_helper_test_dlopen
#define dlsym root_helper_test_dlsym
#define dlclose root_helper_test_dlclose
#include "../src/ios/Diagnostics/RootManagementClient.c"
#undef dlclose
#undef dlsym
#undef dlopen
#undef realpath
#undef _NSGetExecutablePath

static char fixture_root[PATH_MAX];
static char executable_path[PATH_MAX];
static char fixed_bundle_aliases[3][PATH_MAX];
static int fixed_bundle_available[3] = {1, 1, 1};
static unsigned int fixed_bundle_resolution_count;
static char roothide_library[PATH_MAX];
static char roothide_bundle[PATH_MAX];
static int roothide_library_available;
static int roothide_symbol_available = 1;
static int roothide_result_available = 1;
static int roothide_handle;
static unsigned int roothide_load_count;
static unsigned int roothide_close_count;

static const char *root_helper_test_jbroot(const char *path) {
    assert(strcmp(path, "/Applications/MinisRoot.app") == 0);
    return roothide_result_available ? roothide_bundle : NULL;
}

static void *root_helper_test_dlopen(const char *path, int flags) {
    assert(flags == (RTLD_NOW | RTLD_LOCAL));
    ++roothide_load_count;
    return roothide_library_available && strcmp(path, roothide_library) == 0 ? &roothide_handle : NULL;
}

static void *root_helper_test_dlsym(void *handle, const char *name) {
    assert(handle == &roothide_handle);
    assert(strcmp(name, "jbroot") == 0);
    return roothide_symbol_available ? (void *)root_helper_test_jbroot : NULL;
}

static int root_helper_test_dlclose(void *handle) {
    assert(handle == &roothide_handle);
    ++roothide_close_count;
    return 0;
}

static int root_helper_test_get_executable_path(char *buffer, uint32_t *capacity) {
    size_t length = strlen(executable_path) + 1;
    if (length > *capacity) {
        *capacity = (uint32_t)length;
        return -1;
    }
    memcpy(buffer, executable_path, length);
    return 0;
}

static char *root_helper_test_realpath(const char *source, char *resolved) {
    static const char *const fixed_roots[] = {
        "/Applications/MinisRoot.app",
        "/var/jb/Applications/MinisRoot.app",
        "/private/var/jb/Applications/MinisRoot.app",
    };
    for (size_t index = 0; index < sizeof(fixed_roots) / sizeof(fixed_roots[0]); ++index) {
        if (strcmp(source, fixed_roots[index]) != 0) continue;
        ++fixed_bundle_resolution_count;
        if (!fixed_bundle_available[index]) {
            errno = ENOENT;
            return NULL;
        }
        return realpath(fixed_bundle_aliases[index], resolved);
    }
    return realpath(source, resolved);
}

static void fixture_path(char output[PATH_MAX], const char *relative) {
    int length = snprintf(output, PATH_MAX, "%s/%s", fixture_root, relative);
    assert(length > 0 && length < PATH_MAX);
}

static void mkdir_parents(const char *path) {
    char copy[PATH_MAX];
    size_t length = strlen(path);
    assert(length < sizeof(copy));
    memcpy(copy, path, length + 1);
    for (char *cursor = copy + strlen(fixture_root) + 1; *cursor; ++cursor) {
        if (*cursor != '/') continue;
        *cursor = '\0';
        if (mkdir(copy, 0755) != 0) assert(errno == EEXIST);
        *cursor = '/';
    }
    if (mkdir(copy, 0755) != 0) assert(errno == EEXIST);
}

static void entry_path(char path[PATH_MAX], const char *relative) {
    fixture_path(path, relative);
    char *slash = strrchr(path, '/');
    assert(slash != NULL);
    *slash = '\0';
    mkdir_parents(path);
    *slash = '/';
}

static void make_file(const char *relative) {
    char path[PATH_MAX];
    entry_path(path, relative);
    int descriptor = open(path, O_CREAT | O_EXCL | O_WRONLY, 0755);
    assert(descriptor >= 0);
    assert(write(descriptor, "fixture", 7) == 7);
    assert(close(descriptor) == 0);
    assert(chmod(path, 0755) == 0);
}

static void make_symlink(const char *target, const char *relative) {
    char path[PATH_MAX];
    entry_path(path, relative);
    assert(symlink(target, path) == 0);
}

static void set_executable(const char *relative) {
    fixture_path(executable_path, relative);
}

static void expect_valid(const char *relative_executable, const char *relative_helper) {
    char expected[PATH_MAX], helper[PATH_MAX], resolved[PATH_MAX];
    set_executable(relative_executable);
    fixture_path(helper, relative_helper);
    assert(realpath(helper, expected) != NULL);
    assert(helper_path(resolved) == 0);
    assert(strcmp(expected, resolved) == 0);
}

static void expect_error(const char *relative_executable, int expected_error) {
    char resolved[PATH_MAX];
    set_executable(relative_executable);
    assert(helper_path(resolved) == expected_error);
}

int main(int argc, char **argv) {
    assert(argc == 2);
    assert(realpath(argv[1], fixture_root) != NULL);

    make_file("rootful/Applications/MinisRoot.app/Minis");
    make_file("rootful/Applications/MinisRoot.app/minis-rootprobe");
    fixture_path(fixed_bundle_aliases[0], "rootful/Applications/MinisRoot.app");
    expect_valid("rootful/Applications/MinisRoot.app/Minis",
                 "rootful/Applications/MinisRoot.app/minis-rootprobe");

    make_file("rootless/private/preboot/FixtureBoot/jb/Applications/MinisRoot.app/Minis");
    make_file("rootless/private/preboot/FixtureBoot/jb/Applications/MinisRoot.app/minis-rootprobe");
    make_symlink("private/var", "rootless/var");
    make_symlink("../preboot/FixtureBoot/jb", "rootless/private/var/jb");
    fixture_path(fixed_bundle_aliases[1], "rootless/var/jb/Applications/MinisRoot.app");
    fixture_path(fixed_bundle_aliases[2], "rootless/private/var/jb/Applications/MinisRoot.app");
    expect_valid("rootless/var/jb/Applications/MinisRoot.app/Minis",
                 "rootless/private/preboot/FixtureBoot/jb/Applications/MinisRoot.app/minis-rootprobe");
    fixed_bundle_available[1] = 0;
    expect_valid("rootless/private/var/jb/Applications/MinisRoot.app/Minis",
                 "rootless/private/preboot/FixtureBoot/jb/Applications/MinisRoot.app/minis-rootprobe");
    fixed_bundle_available[1] = 1;
    assert(roothide_load_count == 0);

    make_file("roothide/random-root/Applications/MinisRoot.app/Minis");
    make_file("roothide/random-root/Applications/MinisRoot.app/minis-rootprobe");
    make_file("roothide/random-root/usr/lib/libroothide.dylib");
    make_symlink("../..", "roothide/random-root/Applications/MinisRoot.app/.jbroot");
    fixture_path(roothide_bundle, "roothide/random-root/Applications/MinisRoot.app");
    fixture_path(roothide_library, "roothide/random-root/Applications/MinisRoot.app/.jbroot/usr/lib/libroothide.dylib");
    expect_error("roothide/random-root/Applications/MinisRoot.app/Minis", EINVAL);
    roothide_library_available = 1;
    roothide_symbol_available = 0;
    expect_error("roothide/random-root/Applications/MinisRoot.app/Minis", EINVAL);
    roothide_symbol_available = 1;
    roothide_result_available = 0;
    expect_error("roothide/random-root/Applications/MinisRoot.app/Minis", EINVAL);
    roothide_result_available = 1;
    expect_valid("roothide/random-root/Applications/MinisRoot.app/Minis",
                 "roothide/random-root/Applications/MinisRoot.app/minis-rootprobe");
    fixture_path(roothide_bundle, "rootful/Applications/MinisRoot.app");
    expect_error("roothide/random-root/Applications/MinisRoot.app/Minis", EINVAL);
    fixture_path(roothide_bundle, "roothide/random-root/Applications/MinisRoot.app");
    assert(roothide_close_count == 4);
    roothide_library_available = 0;

    make_file("container/FixtureUUID/Minis.app/Minis");
    make_file("container/FixtureUUID/Minis.app/minis-rootprobe");
    unsigned int before_trollstore = fixed_bundle_resolution_count;
    unsigned int before_trollstore_loads = roothide_load_count;
    expect_valid("container/FixtureUUID/Minis.app/Minis",
                 "container/FixtureUUID/Minis.app/minis-rootprobe");
    assert(fixed_bundle_resolution_count == before_trollstore);
    assert(roothide_load_count == before_trollstore_loads);

    make_file("untrusted/MinisRoot.app/Minis");
    make_file("untrusted/MinisRoot.app/minis-rootprobe");
    expect_error("untrusted/MinisRoot.app/Minis", EINVAL);
    make_file("rootful/Applications/MinisRoot.app.extra/Minis");
    make_file("rootful/Applications/MinisRoot.app.extra/minis-rootprobe");
    expect_error("rootful/Applications/MinisRoot.app.extra/Minis", EINVAL);

    fixed_bundle_available[0] = fixed_bundle_available[1] = fixed_bundle_available[2] = 0;
    expect_error("rootful/Applications/MinisRoot.app/Minis", EINVAL);
    fixed_bundle_available[0] = fixed_bundle_available[1] = fixed_bundle_available[2] = 1;

    char helper[PATH_MAX], target[PATH_MAX];
    fixture_path(helper, "rootful/Applications/MinisRoot.app/minis-rootprobe");
    fixture_path(target, "untrusted/MinisRoot.app/minis-rootprobe");
    assert(unlink(helper) == 0);
    assert(symlink(target, helper) == 0);
    expect_error("rootful/Applications/MinisRoot.app/Minis", EPERM);

    fixture_path(helper, "rootless/private/preboot/FixtureBoot/jb/Applications/MinisRoot.app/minis-rootprobe");
    assert(unlink(helper) == 0);
    assert(symlink(target, helper) == 0);
    expect_error("rootless/var/jb/Applications/MinisRoot.app/Minis", EPERM);
    fixture_path(helper, "roothide/random-root/Applications/MinisRoot.app/minis-rootprobe");
    assert(unlink(helper) == 0);
    assert(symlink(target, helper) == 0);
    roothide_library_available = 1;
    expect_error("roothide/random-root/Applications/MinisRoot.app/Minis", EPERM);
    puts("PASS production helper path: rootless preboot symlinks, RootHide fixed jbroot ABI (mock library), TrollStore, exact roots and helper symlink refusal; no process spawned");
    return 0;
}
