#import "MinisAutomationSession.h"
#include <CoreFoundation/CoreFoundation.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <mach/mach_time.h>
#include <pthread.h>
#include <string.h>
#include <TargetConditionals.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef OMMA_BUILD_COMMIT
#define OMMA_BUILD_COMMIT "0000000000000000000000000000000000000000"
#endif
#ifndef OMMA_BUILD_RUN_ID
#define OMMA_BUILD_RUN_ID "0"
#endif
#define OMMA_CONTROL_PARENT "/private/var/mobile/Library/OpenMinisAutomation"
#define OMMA_CONTROL_FILE "session.json"

static pthread_mutex_t omma_lock = PTHREAD_MUTEX_INITIALIZER;
static NSDictionary *omma_session;
static NSMutableDictionary<NSNumber *, NSDictionary *> *omma_requests;
static uint64_t omma_generation;
static uint64_t omma_next_ticket;
static uint64_t omma_interactive_ticket;
static double omma_boot;
static double omma_last_created;
static NSString *omma_last_session_id;
static _Thread_local uint64_t omma_thread_generation;
static _Thread_local uint64_t omma_thread_ticket;
static _Thread_local int omma_thread_socket = -1;
static _Thread_local char omma_thread_session_id[37];
static _Thread_local char omma_thread_request_id[37];
#if OMMA_SESSION_TESTS
static double omma_test_now;
#endif

double OMMANow(void) {
#if OMMA_SESSION_TESTS
    if (omma_test_now > 0) return omma_test_now;
#endif
    mach_timebase_info_data_t timebase;
    mach_timebase_info(&timebase);
    return (double)mach_continuous_time() * (double)timebase.numer / (double)timebase.denom / 1e9;
}

static BOOL omma_text(id value, NSString *pattern) {
    return [value isKindOfClass:NSString.class] &&
        [value rangeOfString:pattern options:NSRegularExpressionSearch].location != NSNotFound;
}

static BOOL omma_number(id value) {
    return [value isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)value) != CFBooleanGetTypeID()
        && isfinite([value doubleValue]);
}

NSDictionary *OMMAParseSession(NSData *data, double boot, double now, NSString *commit, NSString *run) {
    if (data.length == 0 || data.length > 4096) return nil;
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (!json) return nil;
    // This protocol is a flat object with eight unescaped ASCII field names.
    // Count JSON key tokens before Foundation can collapse duplicate fields.
    NSRegularExpression *fieldPattern = [NSRegularExpression regularExpressionWithPattern:@"\"((?:[^\"\\\\]|\\\\.)*)\"\\s*:" options:0 error:NULL];
    NSArray<NSTextCheckingResult *> *fields = [fieldPattern matchesInString:json options:0 range:NSMakeRange(0, json.length)];
    if (fields.count != 8) return nil;
    NSMutableSet *seenFields = [NSMutableSet set];
    for (NSTextCheckingResult *field in fields) {
        NSString *key = [json substringWithRange:[field rangeAtIndex:1]];
        if ([key containsString:@"\\"] || [seenFields containsObject:key]) return nil;
        [seenFields addObject:key];
    }
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    if (![value isKindOfClass:NSDictionary.class]) return nil;
    NSDictionary *s = value;
    NSSet *keys = [NSSet setWithArray:@[@"schema", @"id", @"token", @"created", @"expires", @"tools_version",
                                      @"source_commit", @"actions_run_id"]];
    if (![[NSSet setWithArray:s.allKeys] isEqualToSet:keys] || !omma_number(s[@"schema"])
        || [s[@"schema"] doubleValue] != 1 || !omma_number(s[@"tools_version"])
        || [s[@"tools_version"] doubleValue] != 1) return nil;
    if (!omma_text(s[@"id"], @"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
        || ![[NSUUID alloc] initWithUUIDString:s[@"id"]] || !omma_text(s[@"token"], @"^[0-9a-f]{64}$")
        || !omma_text(s[@"source_commit"], @"^[0-9a-f]{40}$")
        || !omma_text(s[@"actions_run_id"], @"^[1-9][0-9]*$")
        || ![s[@"source_commit"] isEqualToString:commit] || ![s[@"actions_run_id"] isEqualToString:run]) return nil;
    if (!omma_number(s[@"created"]) || !omma_number(s[@"expires"])) return nil;
    double created = [s[@"created"] doubleValue], expires = [s[@"expires"] doubleValue];
    if (!(created >= boot && created <= now && expires > now && expires > created && expires - created <= 600.0)) return nil;
    return [s copy];
}

static NSData *omma_read_control_at(const char *parentPath, uid_t owner) {
    int directory = open(parentPath, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (directory < 0) return nil;
    struct stat parent, before, after;
    if (fstat(directory, &parent) != 0 || !S_ISDIR(parent.st_mode) || parent.st_uid != owner
        || (parent.st_mode & 07777) != 0700) { close(directory); return nil; }
    int file = openat(directory, OMMA_CONTROL_FILE, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    close(directory);
    if (file < 0) return nil;
    if (fstat(file, &before) != 0 || !S_ISREG(before.st_mode) || before.st_uid != owner || before.st_nlink != 1
        || (before.st_mode & 07777) != 0600 || before.st_size <= 0 || before.st_size > 4096) { close(file); return nil; }
    NSMutableData *data = [NSMutableData dataWithLength:(NSUInteger)before.st_size];
    size_t received = 0;
    while (received < data.length) {
        ssize_t count = read(file, (char *)data.mutableBytes + received, data.length - received);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { close(file); return nil; }
        received += (size_t)count;
    }
    char extra;
    BOOL stable = read(file, &extra, 1) == 0 && fstat(file, &after) == 0 && before.st_dev == after.st_dev
        && before.st_ino == after.st_ino && before.st_size == after.st_size && before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec
        && before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec;
    close(file);
    return stable ? data : nil;
}

static NSData *omma_read_control(void) { return omma_read_control_at(OMMA_CONTROL_PARENT, 501); }

void OMMASessionInitialize(void) {
    pthread_mutex_lock(&omma_lock);
    if (omma_boot == 0) {
        omma_boot = OMMANow();
        omma_requests = [NSMutableDictionary dictionary];
    }
    pthread_mutex_unlock(&omma_lock);
}

static void omma_revoke_locked(void) {
    omma_session = nil;
    ++omma_generation;
    // Retain outstanding tickets until their owners finish. Old blocks see an
    // obsolete generation; a new grant cannot make those blocks valid again.
}

void OMMASessionStop(void) {
    pthread_mutex_lock(&omma_lock);
    omma_revoke_locked();
    pthread_mutex_unlock(&omma_lock);
}

static BOOL omma_accept(NSData *data, double boot, double now) {
    NSDictionary *parsed = OMMAParseSession(data, boot, now, @OMMA_BUILD_COMMIT, @OMMA_BUILD_RUN_ID);
    pthread_mutex_lock(&omma_lock);
    if (parsed && omma_session && [parsed isEqualToDictionary:omma_session]) {
        pthread_mutex_unlock(&omma_lock);
        return YES; // duplicate START cannot replace the active generation
    }
    if (parsed && ([parsed[@"created"] doubleValue] <= omma_last_created ||
                   [parsed[@"id"] isEqualToString:omma_last_session_id])) parsed = nil;
    omma_revoke_locked();
    omma_session = parsed;
    if (parsed) {
        omma_last_created = [parsed[@"created"] doubleValue];
        omma_last_session_id = parsed[@"id"];
    }
    pthread_mutex_unlock(&omma_lock);
    return parsed != nil;
}

BOOL OMMASessionStart(void) {
    OMMASessionInitialize();
    return omma_accept(omma_read_control(), omma_boot, OMMANow());
}

BOOL OMMASessionActive(void) {
    pthread_mutex_lock(&omma_lock);
    BOOL active = omma_session != nil && OMMANow() < [omma_session[@"expires"] doubleValue];
    if (!active && omma_session) omma_revoke_locked();
    pthread_mutex_unlock(&omma_lock);
    return active;
}

static BOOL omma_constant_equal(NSString *actual, NSString *expected) {
    NSData *a = [actual dataUsingEncoding:NSASCIIStringEncoding], *b = [expected dataUsingEncoding:NSASCIIStringEncoding];
    if (a.length != 64 || b.length != 64) return NO;
    const unsigned char *x = a.bytes, *y = b.bytes;
    unsigned char difference = 0;
    for (NSUInteger i = 0; i < 64; ++i) difference |= x[i] ^ y[i];
    return difference == 0;
}

BOOL OMMAAuthorize(NSString *authorization, int socket) {
    omma_thread_generation = 0;
    omma_thread_ticket = 0;
    omma_thread_socket = socket;
    strlcpy(omma_thread_request_id, NSUUID.UUID.UUIDString.lowercaseString.UTF8String, sizeof(omma_thread_request_id));
    omma_thread_session_id[0] = '\0';
    if (![authorization isKindOfClass:NSString.class] || ![authorization hasPrefix:@"Bearer "] || authorization.length != 71) return NO;
    NSData *data = omma_read_control();
    NSDictionary *disk = OMMAParseSession(data, omma_boot, OMMANow(), @OMMA_BUILD_COMMIT, @OMMA_BUILD_RUN_ID);
    pthread_mutex_lock(&omma_lock);
    BOOL valid = disk && omma_session && [disk isEqualToDictionary:omma_session]
        && OMMANow() < [omma_session[@"expires"] doubleValue]
        && omma_constant_equal([authorization substringFromIndex:7], omma_session[@"token"]);
    if (valid) {
        omma_thread_generation = omma_generation;
        strlcpy(omma_thread_session_id, [omma_session[@"id"] UTF8String], sizeof(omma_thread_session_id));
    }
    else if (!disk || ![disk isEqualToDictionary:omma_session]) omma_revoke_locked();
    pthread_mutex_unlock(&omma_lock);
    return valid;
}

int OMMACurrentSocket(void) { return omma_thread_socket; }

uint64_t OMMARequestBegin(BOOL interactive, int socket) {
    pthread_mutex_lock(&omma_lock);
    uint64_t ticket = 0;
    if (omma_session && omma_thread_generation == omma_generation
        && OMMANow() < [omma_session[@"expires"] doubleValue] && (!interactive || omma_interactive_ticket == 0)) {
        ticket = ++omma_next_ticket;
        omma_requests[@(ticket)] = @{@"generation": @(omma_generation), @"deadline": @(OMMANow() + 35.0), @"socket": @(socket),
            @"session_id": omma_session[@"id"], @"request_id": omma_thread_request_id[0]
                ? [NSString stringWithUTF8String:omma_thread_request_id] : NSUUID.UUID.UUIDString.lowercaseString};
        if (interactive) omma_interactive_ticket = ticket;
    }
    omma_thread_ticket = ticket;
    pthread_mutex_unlock(&omma_lock);
    return ticket;
}

void OMMARequestEnd(uint64_t ticket) {
    pthread_mutex_lock(&omma_lock);
    [omma_requests removeObjectForKey:@(ticket)];
    if (omma_interactive_ticket == ticket) omma_interactive_ticket = 0;
    pthread_mutex_unlock(&omma_lock);
    omma_thread_ticket = 0;
}

BOOL OMMARequestAlive(void) {
    pthread_mutex_lock(&omma_lock);
    NSDictionary *request = omma_requests[@(omma_thread_ticket)];
    double now = OMMANow();
    BOOL alive = omma_session && request && [request[@"generation"] unsignedLongLongValue] == omma_generation
        && now < [request[@"deadline"] doubleValue] && now < [omma_session[@"expires"] doubleValue];
    int socket = [request[@"socket"] intValue];
    if (alive && socket >= 0) {
        char byte;
        ssize_t count = recv(socket, &byte, 1, MSG_PEEK | MSG_DONTWAIT);
        if (count == 0 || (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) alive = NO;
    }
    pthread_mutex_unlock(&omma_lock);
    return alive;
}

int OMMAInteractionSleep(unsigned int microseconds) {
    unsigned int remaining = microseconds;
    while (remaining > 0 && OMMARequestAlive()) {
        useconds_t interval = (useconds_t)MIN(remaining, 10000u);
        usleep(interval);
        remaining -= interval;
    }
    // Return to the ordinary sequence, whose next up/end remains permitted.
    // A following move/down checks the expired ticket and sends touch-end.
    return 0;
}

static dispatch_block_t omma_carry(dispatch_block_t block) {
    uint64_t generation = omma_thread_generation, ticket = omma_thread_ticket;
    int socket = omma_thread_socket;
    NSString *sessionID = [NSString stringWithUTF8String:omma_thread_session_id], *requestID = [NSString stringWithUTF8String:omma_thread_request_id];
    return ^{
        uint64_t previousGeneration = omma_thread_generation, previousTicket = omma_thread_ticket;
        int previousSocket = omma_thread_socket;
        char previousSessionID[37], previousRequestID[37];
        memcpy(previousSessionID, omma_thread_session_id, sizeof(previousSessionID));
        memcpy(previousRequestID, omma_thread_request_id, sizeof(previousRequestID));
        omma_thread_generation = generation;
        omma_thread_ticket = ticket;
        omma_thread_socket = socket;
        strlcpy(omma_thread_session_id, sessionID.UTF8String, sizeof(omma_thread_session_id));
        strlcpy(omma_thread_request_id, requestID.UTF8String, sizeof(omma_thread_request_id));
        @try { block(); }
        @finally { omma_thread_generation = previousGeneration; omma_thread_ticket = previousTicket; omma_thread_socket = previousSocket;
            memcpy(omma_thread_session_id, previousSessionID, sizeof(previousSessionID));
            memcpy(omma_thread_request_id, previousRequestID, sizeof(previousRequestID)); }
    };
}

void OMMADispatchAsync(dispatch_queue_t queue, dispatch_block_t block) { dispatch_async(queue, omma_carry(block)); }
void OMMADispatchSync(dispatch_queue_t queue, dispatch_block_t block) { dispatch_sync(queue, omma_carry(block)); }

BOOL OMMAToolAllowed(NSString *name) {
    static NSSet *names;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ names = [NSSet setWithArray:@[@"get_device_info", @"get_screen_info", @"screenshot",
        @"get_frontmost_app", @"list_apps", @"list_running_apps", @"get_ui_elements", @"get_element_at_point",
        @"wait_for_element", @"wait_for_disappear", @"tap_screen", @"tap_element", @"swipe_screen", @"long_press",
        @"double_tap", @"drag_and_drop", @"input_text", @"type_text", @"press_key", @"launch_app", @"kill_app", @"press_home"]]; });
    return [names containsObject:name];
}

BOOL OMMAToolInteractive(NSString *name) {
    return [@[@"tap_screen", @"tap_element", @"swipe_screen", @"long_press", @"double_tap", @"drag_and_drop",
              @"input_text", @"type_text", @"press_key", @"launch_app", @"kill_app", @"press_home"] containsObject:name];
}

NSDictionary *OMMAProvenance(void) {
    pthread_mutex_lock(&omma_lock);
#if TARGET_OS_SIMULATOR
    NSString *runtime = @"simulator";
#elif TARGET_OS_IOS
    NSString *runtime = @"physical-device";
#else
    NSString *runtime = @"unknown";
#endif
    NSDictionary *request = omma_requests[@(omma_thread_ticket)];
    NSDictionary *result = @{@"schema": @1, @"source_commit": @OMMA_BUILD_COMMIT, @"actions_run_id": @OMMA_BUILD_RUN_ID,
        @"upstream_commit": @"62c4464f688c8f269419924b1a19c996de75f175",
        @"session_id": request[@"session_id"] ?: [NSString stringWithUTF8String:omma_thread_session_id],
        @"request_id": request[@"request_id"] ?: (omma_thread_request_id[0] ? [NSString stringWithUTF8String:omma_thread_request_id] : NSUUID.UUID.UUIDString.lowercaseString),
        @"generation": @(omma_generation), @"runtime": runtime, @"host_bundle": @"com.apple.springboard",
        @"uid": @(getuid()), @"euid": @(geteuid()),
        @"authorized": @(request && omma_session && [request[@"generation"] unsignedLongLongValue] == omma_generation
                          && OMMANow() < [omma_session[@"expires"] doubleValue])};
    pthread_mutex_unlock(&omma_lock);
    return result;
}

NSDictionary *OMMATraceReply(NSDictionary *reply) {
    NSMutableDictionary *observed = [reply mutableCopy];
    if ([reply[@"result"] isKindOfClass:NSDictionary.class]) {
        NSMutableDictionary *result = [reply[@"result"] mutableCopy];
        NSMutableDictionary *meta = [result[@"_meta"] isKindOfClass:NSDictionary.class] ? [result[@"_meta"] mutableCopy] : [NSMutableDictionary dictionary];
        meta[@"openminis"] = OMMAProvenance();
        result[@"_meta"] = meta;
        if (!result[@"isError"]) result[@"isError"] = @NO;
        observed[@"result"] = result;
    } else if ([reply[@"error"] isKindOfClass:NSDictionary.class]) {
        NSMutableDictionary *error = [reply[@"error"] mutableCopy];
        error[@"data"] = @{@"openminis": OMMAProvenance()};
        observed[@"error"] = error;
    }
    return observed;
}

#if OMMA_SESSION_TESTS
BOOL OMMATestAccept(NSData *data, double boot, double now) {
    OMMASessionInitialize();
    omma_test_now = now;
    BOOL accepted = omma_accept(data, boot, now);
    omma_thread_generation = omma_generation;
    return accepted;
}
void OMMATestSetNow(double now) { omma_test_now = now; }
NSData *OMMATestReadControl(const char *path, uid_t owner) { return omma_read_control_at(path, owner); }
#endif
