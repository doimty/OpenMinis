#if defined(MINIS_ROOT_MANAGEMENT) && MINIS_ROOT_MANAGEMENT
#import <UIKit/UIKit.h>
#import "DeviceMCPOffload.h"
#import "DeviceMCPNativeBridge.h"
#import "NativeOffloadUtils.h"
#include "kernel/native_offload.h"
#include <string.h>
#include <stdatomic.h>

static _Atomic uint64_t cancellation_epoch;

uint64_t OMDeviceMCPCancellationEpoch(void) {
    return atomic_load(&cancellation_epoch);
}

static bool device_mcp_abort(int sig) {
    (void)sig;
    atomic_fetch_add(&cancellation_epoch, 1);
    return true;
}

static NSString *bounded_argument(const char *argument, size_t maximum) {
    if (!argument || strnlen(argument, maximum + 1) > maximum) return nil;
    return [NSString stringWithUTF8String:argument];
}

static int device_mcp_call(int argc, char **argv, int stdin_fd, int stdout_fd, int stderr_fd) {
    @autoreleasepool {
        (void)stdin_fd;
        (void)stderr_fd;
        uint64_t epoch = OMDeviceMCPCancellationEpoch();
        if (argc == 2 && strcmp(argv[1], "status") == 0) {
            BOOL enabled = [DeviceMCPNativeBridge available];
            noff_emit_json(stdout_fd, @{@"enabled": @(enabled)}, YES, NO);
            return 0;
        }
        NSString *identifier = argc >= 3 && argc <= 4 ? bounded_argument(argv[1], 36) : nil;
        NSString *operation = identifier ? bounded_argument(argv[2], 24) : nil;
        NSString *value = operation && argc == 4 ? bounded_argument(argv[3], 3) : nil;
        if (!identifier || !operation || (argc == 4 && !value)) {
            noff_emit_json(stdout_fd, @{@"ok": @NO, @"error": @"invalid"}, YES, NO);
            return NOFF_EXIT_INVALID_ARGS;
        }
        NSDictionary *reply = [DeviceMCPNativeBridge request:identifier operation:operation value:value epoch:epoch];
        noff_emit_json(stdout_fd, reply, YES, NO);
        return [reply[@"ok"] boolValue] ? NOFF_EXIT_SUCCESS : NOFF_EXIT_ERROR;
    }
}

void device_mcp_offload_register(void) {
    if (![[NSBundle mainBundle].bundleIdentifier isEqualToString:@"com.openminis.app.root"]) return;
    if (native_offload_add_handler("minis-device-call", device_mcp_call) == 0 &&
        native_offload_set_abort_handler("minis-device-call", device_mcp_abort) == 0) {
        noff_ensure_guest_stub("/usr/local/bin/minis-device-call");
    }
}
#endif
