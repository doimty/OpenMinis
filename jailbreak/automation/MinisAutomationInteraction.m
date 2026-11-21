#import "MinisAutomationInteraction.h"
#import "MinisAutomationSession.h"
#import "ScreenManager.h"
#include <unistd.h>

BOOL OMMAInteractionAlive(void) {
    if (!OMMARequestAlive()) return NO;
    NSDictionary *state = [[ScreenManager sharedInstance] deviceInteractionState];
    return OMMARequestAlive() && [state[@"locked_known"] boolValue] && [state[@"screen_on_known"] boolValue]
        && ![state[@"locked"] boolValue] && [state[@"screen_on"] boolValue]
        && ![state[@"lock_screen_visible"] boolValue];
}

int OMMAEventSleep(unsigned int microseconds) {
    unsigned int remaining = microseconds;
    while (remaining > 0 && OMMAInteractionAlive()) {
        useconds_t interval = (useconds_t)MIN(remaining, 10000u);
        usleep(interval);
        remaining -= interval;
    }
    return 0;
}
