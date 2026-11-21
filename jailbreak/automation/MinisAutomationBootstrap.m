#import <UIKit/UIKit.h>
#import <CoreFoundation/CoreFoundation.h>
#import "MCPServer.h"
#import "MinisAutomationSession.h"

static dispatch_source_t omma_expiry_timer;
static void omma_control(CFNotificationCenterRef center, void *observer, CFStringRef name,
                         const void *object, CFDictionaryRef userInfo) {
    (void)center; (void)observer; (void)object; (void)userInfo;
    if (CFEqual(name, CFSTR("com.openminis.automation.start"))) {
        if (OMMASessionStart()) [[MCPServer sharedInstance] startOnPort:8091];
        else [[MCPServer sharedInstance] stop];
    } else {
        OMMASessionStop();
        [[MCPServer sharedInstance] stop];
    }
}

__attribute__((constructor)) static void omma_initialize(void) {
    @autoreleasepool {
        if (![[NSProcessInfo processInfo].processName isEqualToString:@"SpringBoard"]) return;
        OMMASessionInitialize();
        CFNotificationCenterRef center = CFNotificationCenterGetDarwinNotifyCenter();
        CFNotificationCenterAddObserver(center, NULL, omma_control, CFSTR("com.openminis.automation.start"), NULL,
                                        CFNotificationSuspensionBehaviorDeliverImmediately);
        CFNotificationCenterAddObserver(center, NULL, omma_control, CFSTR("com.openminis.automation.stop"), NULL,
                                        CFNotificationSuspensionBehaviorDeliverImmediately);
        dispatch_queue_t queue = dispatch_queue_create("com.openminis.automation.expiry", DISPATCH_QUEUE_SERIAL);
        omma_expiry_timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, queue);
        dispatch_source_set_timer(omma_expiry_timer, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 4), NSEC_PER_SEC / 4,
                                  NSEC_PER_MSEC * 20);
        dispatch_source_set_event_handler(omma_expiry_timer, ^{
            if (!OMMASessionActive() && [MCPServer sharedInstance].running) [[MCPServer sharedInstance] stop];
        });
        dispatch_resume(omma_expiry_timer);
        // No preference, listener, token or outstanding control file is read
        // here. A fresh user action and START notification are mandatory.
    }
}
