#import <Foundation/Foundation.h>
#import "MinisAutomationSession.h"
#import "MinisAutomationPolicy.h"
#include <assert.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

static NSMutableDictionary *grant(void) {
    return [@{@"schema": @1, @"id": @"53b5ec04-788d-4a8f-983c-9df8607cd1af",
              @"token": @"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
              @"created": @101, @"expires": @281, @"tools_version": @1,
              @"source_commit": @"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", @"actions_run_id": @"12345"} mutableCopy];
}
static NSData *data(NSDictionary *value) { return [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingSortedKeys error:NULL]; }
static NSDictionary *parse(NSDictionary *value) {
    return OMMAParseSession(data(value), 100, 102, @"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", @"12345");
}

int main(int argc, char **argv) {
    @autoreleasepool {
        assert(argc == 2);
        OMMASessionInitialize();
        assert(!OMMASessionActive());
        assert(!OMMAAuthorize(@"Bearer wrong", -1));
        assert(parse(grant()));
        NSMutableDictionary *g = grant();
        for (NSString *key in g.allKeys) {
            NSMutableDictionary *missing = [g mutableCopy]; [missing removeObjectForKey:key]; assert(!parse(missing));
        }
        g[@"extra"] = @1; assert(!parse(g)); g = grant();
        g[@"schema"] = @YES; assert(!parse(g)); g = grant();
        g[@"tools_version"] = @2; assert(!parse(g)); g = grant();
        g[@"created"] = @99; assert(!parse(g)); g = grant();
        g[@"created"] = @103; assert(!parse(g)); g = grant();
        g[@"expires"] = @102; assert(!parse(g)); g = grant();
        g[@"expires"] = @702; assert(!parse(g)); g = grant();
        g[@"token"] = [g[@"token"] uppercaseString]; assert(!parse(g)); g = grant();
        g[@"source_commit"] = @"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"; assert(!parse(g)); g = grant();
        g[@"actions_run_id"] = @"12346"; assert(!parse(g)); g = grant();
        g[@"id"] = @"not-a-uuid"; assert(!parse(g)); g = grant();
        NSMutableString *duplicate = [[NSString alloc] initWithData:data(g) encoding:NSUTF8StringEncoding].mutableCopy;
        [duplicate insertString:@"\"schema\":1," atIndex:1];
        assert(!OMMAParseSession([duplicate dataUsingEncoding:NSUTF8StringEncoding], 100, 102,
            @"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", @"12345"));

        assert(OMMATestAccept(data(grant()), 100, 102));
        assert(OMMASessionActive());
        NSNumber *activeGeneration = OMMAProvenance()[@"generation"];
        assert(OMMATestAccept(data(grant()), 100, 102));
        assert([activeGeneration isEqual:OMMAProvenance()[@"generation"]]);
        uint64_t first = OMMARequestBegin(YES, -1); assert(first && OMMARequestAlive());
        NSString *requestID = OMMAProvenance()[@"request_id"];
        assert([[NSUUID alloc] initWithUUIDString:requestID] && [requestID characterAtIndex:14] == '4');
        assert([requestID isEqual:OMMAProvenance()[@"request_id"]]);
        assert([OMMAProvenance()[@"source_commit"] isEqual:@"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]);
        assert(!OMMAProvenance()[@"token"]);
        dispatch_queue_t queue = dispatch_queue_create("com.openminis.automation.test", DISPATCH_QUEUE_SERIAL);
        OMMADispatchSync(queue, ^{ assert(OMMARequestAlive()); assert(OMMARequestBegin(YES, -1) == 0); });
        assert(OMMARequestAlive());
        dispatch_suspend(queue);
        dispatch_semaphore_t finished = dispatch_semaphore_create(0);
        OMMADispatchAsync(queue, ^{ assert(!OMMARequestAlive()); dispatch_semaphore_signal(finished); });
        OMMASessionStop(); assert(!OMMARequestAlive() && !OMMASessionActive());
        double cancelledSleep = OMMANow(); OMMAInteractionSleep(30000000); assert(OMMANow() - cancelledSleep < 0.1);
        assert(!OMMATestAccept(data(grant()), 100, 103)); // STOP consumes the grant even while its file remains
        NSMutableDictionary *fresh = grant();
        fresh[@"id"] = @"3a47ace8-0cf9-4784-8a7c-5ab82604598c"; fresh[@"created"] = @102.5; fresh[@"expires"] = @282.5;
        assert(OMMATestAccept(data(fresh), 100, 103));
        assert(OMMARequestBegin(YES, -1) == 0); // old in-flight interaction still owns its slot
        OMMARequestEnd(first);
        uint64_t second = OMMARequestBegin(YES, -1); assert(second && second != first && OMMARequestAlive());
        dispatch_resume(queue);
        assert(dispatch_semaphore_wait(finished, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
        assert(OMMARequestAlive());
        NSDictionary *reply = OMMATraceReply(@{@"result": @{@"content": @[@{@"type": @"text", @"text": @"ok"}]}});
        assert([reply[@"result"][@"_meta"][@"openminis"][@"authorized"] boolValue]);
        OMMATestSetNow(283); assert(!OMMARequestAlive() && !OMMASessionActive()); OMMARequestEnd(second);
        fresh[@"id"] = @"742f6b8a-735c-4ae1-a307-7b0110e826be"; fresh[@"created"] = @103; fresh[@"expires"] = @283;
        assert(OMMATestAccept(data(fresh), 100, 104));
        int sockets[2]; assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
        uint64_t connected = OMMARequestBegin(YES, sockets[0]); assert(connected && OMMARequestAlive());
        close(sockets[1]); assert(!OMMARequestAlive()); close(sockets[0]); OMMARequestEnd(connected);

        NSArray *allowed = @[@"get_device_info", @"get_screen_info", @"screenshot", @"get_frontmost_app", @"list_apps", @"list_running_apps",
            @"get_ui_elements", @"get_element_at_point", @"wait_for_element", @"wait_for_disappear", @"tap_screen", @"tap_element",
            @"swipe_screen", @"long_press", @"double_tap", @"drag_and_drop", @"input_text", @"type_text", @"press_key", @"launch_app", @"kill_app", @"press_home"];
        for (NSString *name in allowed) assert(OMMAToolAllowed(name));
        for (NSString *name in @[@"run_command", @"write_file", @"read_file", @"list_dir", @"install_app", @"uninstall_app", @"get_syslog", @"wake_and_home", @"get_clipboard"])
            assert(!OMMAToolAllowed(name));
        assert(OMMAHostProtected(@"com.apple.springboard") && OMMAHostProtected(@"com.openminis.app.root"));
        assert(!OMMAHostProtected(@"com.example.test"));
        NSDictionary *schema = @{@"type": @"object", @"properties": @{@"text": @{@"type": @"string"}, @"delay_ms": @{@"type": @"number"}}, @"required": @[@"text"]};
        assert(OMMAArgumentsValid(schema, @{@"text": @"public-marker", @"delay_ms": @10}));
        assert(!OMMAArgumentsValid(schema, @{@"text": @"marker", @"delay_ms": @YES}));
        assert(!OMMAArgumentsValid(schema, @{@"text": @"marker", @"delay_ms": @201}));
        assert(!OMMAArgumentsValid(schema, @{@"text": @"marker", @"command": @"id"}));

        NSString *parent = [NSString stringWithUTF8String:argv[1]];
        assert(chmod(parent.fileSystemRepresentation, 0700) == 0);
        NSString *file = [parent stringByAppendingPathComponent:@"session.json"];
        assert([data(grant()) writeToFile:file atomically:NO]); assert(chmod(file.fileSystemRepresentation, 0600) == 0);
        assert(OMMATestReadControl(parent.fileSystemRepresentation, getuid()));
        assert(!OMMATestReadControl(parent.fileSystemRepresentation, getuid() + 1));
        assert(chmod(file.fileSystemRepresentation, 0644) == 0); assert(!OMMATestReadControl(parent.fileSystemRepresentation, getuid()));
        assert(chmod(file.fileSystemRepresentation, 0600) == 0);
        NSString *alias = [parent stringByAppendingPathComponent:@"hardlink"];
        assert(link(file.fileSystemRepresentation, alias.fileSystemRepresentation) == 0);
        assert(!OMMATestReadControl(parent.fileSystemRepresentation, getuid())); assert(unlink(alias.fileSystemRepresentation) == 0);
        assert(unlink(file.fileSystemRepresentation) == 0); assert([data(grant()) writeToFile:alias atomically:NO]);
        assert(chmod(alias.fileSystemRepresentation, 0600) == 0); assert(symlink(alias.fileSystemRepresentation, file.fileSystemRepresentation) == 0);
        assert(!OMMATestReadControl(parent.fileSystemRepresentation, getuid()));
        assert(unlink(file.fileSystemRepresentation) == 0); assert(rename(alias.fileSystemRepresentation, file.fileSystemRepresentation) == 0);
        assert(chmod(parent.fileSystemRepresentation, 0755) == 0); assert(!OMMATestReadControl(parent.fileSystemRepresentation, getuid()));
        puts("PASS production automation session/parser/path policy, default denial, TTL, old generation, dispatch cancellation, disconnect, trace, whitelist and input bounds; no device operations executed");
    }
    return 0;
}
