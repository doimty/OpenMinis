#import <Foundation/Foundation.h>
#import "ScreenManager.h"
#import "MinisAutomationSession.h"
#import "MinisAutomationInteraction.h"
#include <assert.h>
#include <pthread.h>
#include <unistd.h>

static pthread_mutex_t fixture_lock = PTHREAD_MUTEX_INITIALIZER;
static NSDictionary *fixture_state;
static void set_state(NSDictionary *state) {
    pthread_mutex_lock(&fixture_lock); fixture_state = [state copy]; pthread_mutex_unlock(&fixture_lock);
}

@implementation ScreenManager
+ (instancetype)sharedInstance {
    static ScreenManager *instance;
    static dispatch_once_t once;
    dispatch_once(&once, ^{ instance = [ScreenManager new]; });
    return instance;
}
- (NSDictionary *)deviceInteractionState {
    pthread_mutex_lock(&fixture_lock); NSDictionary *state = fixture_state; pthread_mutex_unlock(&fixture_lock);
    return state;
}
@end

static NSData *grant(NSString *session, double created) {
    return [NSJSONSerialization dataWithJSONObject:@{@"schema": @1, @"id": session,
        @"token": @"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        @"created": @(created), @"expires": @(created + 180), @"tools_version": @1,
        @"source_commit": @"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", @"actions_run_id": @"12345"} options:0 error:NULL];
}

static NSDictionary *awake(void) {
    return @{@"locked_known": @YES, @"screen_on_known": @YES, @"locked": @NO, @"screen_on": @YES,
             @"lock_screen_visible": @NO};
}

int main(void) {
    @autoreleasepool {
        set_state(awake());
        OMMASessionInitialize();
        assert(!OMMAInteractionAlive());
        assert(OMMATestAccept(grant(@"17c7b66f-149f-4da2-bff0-f992a38c311c", 101), 100, 102));
        uint64_t first = OMMARequestBegin(YES, -1);
        assert(first && OMMAInteractionAlive());
        double before = NSProcessInfo.processInfo.systemUptime;
        OMMAEventSleep(30000);
        assert(NSProcessInfo.processInfo.systemUptime - before >= 0.02);
        NSArray *blocked = @[
            @{@"locked_known": @YES, @"screen_on_known": @YES, @"locked": @YES, @"screen_on": @YES},
            @{@"locked_known": @YES, @"screen_on_known": @YES, @"locked": @NO, @"screen_on": @NO},
            @{@"locked_known": @NO, @"screen_on_known": @YES, @"locked": NSNull.null, @"screen_on": @YES},
            @{@"locked_known": @YES, @"screen_on_known": @NO, @"locked": @NO, @"screen_on": NSNull.null},
            @{@"locked_known": @YES, @"screen_on_known": @YES, @"locked": @NO, @"screen_on": @YES, @"lock_screen_visible": @YES}, @{}];
        for (NSDictionary *state in blocked) {
            set_state(state); assert(!OMMAInteractionAlive());
            before = NSProcessInfo.processInfo.systemUptime; OMMAEventSleep(30000000);
            assert(NSProcessInfo.processInfo.systemUptime - before < 0.25);
        }
        set_state(awake());
        dispatch_queue_t queue = dispatch_queue_create("com.openminis.automation.interaction-test", DISPATCH_QUEUE_SERIAL);
        dispatch_semaphore_t sleeping = dispatch_semaphore_create(0), released = dispatch_semaphore_create(0);
        __block double elapsed = 0;
        OMMADispatchAsync(queue, ^{
            double began = NSProcessInfo.processInfo.systemUptime;
            dispatch_semaphore_signal(sleeping);
            OMMAEventSleep(30000000);
            elapsed = NSProcessInfo.processInfo.systemUptime - began;
            assert(!OMMAInteractionAlive()); dispatch_semaphore_signal(released);
        });
        assert(dispatch_semaphore_wait(sleeping, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
        usleep(25000);
        set_state(blocked.firstObject);
        assert(dispatch_semaphore_wait(released, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
        assert(elapsed < 0.5); // the production wait observes a lock during its 30-second hold

        set_state(awake()); assert(OMMAInteractionAlive());
        dispatch_suspend(queue);
        OMMADispatchAsync(queue, ^{ assert(!OMMAInteractionAlive()); dispatch_semaphore_signal(released); });
        OMMASessionStop(); assert(!OMMAInteractionAlive());
        assert(OMMATestAccept(grant(@"ce9ae3d1-5604-40c9-a5cf-7618874713e7", 102.5), 100, 103));
        OMMARequestEnd(first);
        uint64_t second = OMMARequestBegin(YES, -1); assert(second && OMMAInteractionAlive());
        dispatch_resume(queue);
        assert(dispatch_semaphore_wait(released, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
        assert(OMMAInteractionAlive());
        OMMASessionStop();
        before = NSProcessInfo.processInfo.systemUptime; OMMAEventSleep(30000000);
        assert(NSProcessInfo.processInfo.systemUptime - before < 0.25);
        OMMARequestEnd(second);
        puts("PASS production per-event interaction gate and cancellation sleep: awake/unlocked, locked/off/unknown, mid-hold lock, STOP and old-generation dispatch; screen observations mocked, no HID sent");
    }
    return 0;
}
