#if defined(MINIS_ROOT_MANAGEMENT) && MINIS_ROOT_MANAGEMENT
#import "AutomationMCPOffload.h"
#import "NativeOffloadUtils.h"
#import "Minis-Swift.h"
#include "kernel/native_offload.h"
#include <errno.h>
#include <unistd.h>

// Internal, read-only transport discovery for utils/config.py. No grant action
// is available through a guest command; the native settings confirmation owns it.
static int automation_mcp_config(int argc, char **argv, int stdin_fd, int stdout_fd, int stderr_fd) {
    @autoreleasepool {
        (void)argv;
        (void)stdin_fd;
        (void)stderr_fd;
        if (argc != 1) return NOFF_EXIT_INVALID_ARGS;
        NSData *data = [[[AutomationMCPBridge transport] stringByAppendingString:@"\n"] dataUsingEncoding:NSUTF8StringEncoding];
        const uint8_t *bytes = data.bytes;
        size_t remaining = data.length;
        while (remaining > 0) {
            ssize_t count = write(stdout_fd, bytes, remaining);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) return NOFF_EXIT_ERROR;
            bytes += count;
            remaining -= (size_t)count;
        }
        return NOFF_EXIT_SUCCESS;
    }
}

void automation_mcp_offload_register(void) {
    if (![[NSBundle mainBundle].bundleIdentifier isEqualToString:@"com.openminis.app.root"]) return;
    if (native_offload_add_handler("minis-automation-config", automation_mcp_config) == 0) {
        noff_ensure_guest_stub("/usr/local/bin/minis-automation-config");
    }
}
#endif
