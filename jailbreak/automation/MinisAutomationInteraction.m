#import "MinisAutomationInteraction.h"
#import "MinisAutomationSession.h"
#import "ScreenManager.h"

BOOL OMMAInteractionAlive(void) {
    if (!OMMARequestAlive()) return NO;
    NSDictionary *state = [[ScreenManager sharedInstance] deviceInteractionState];
    return OMMARequestAlive() && [state[@"locked_known"] boolValue] && [state[@"screen_on_known"] boolValue]
        && ![state[@"locked"] boolValue] && [state[@"screen_on"] boolValue]
        && ![state[@"lock_screen_visible"] boolValue];
}
