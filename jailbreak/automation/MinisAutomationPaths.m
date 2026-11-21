#import "MinisAutomationSession.h"
#include <roothide.h>

NSString *OMMARootPath(NSString *path) {
    const char *resolved = jbroot(path.fileSystemRepresentation);
    return resolved ? [NSString stringWithUTF8String:resolved] : nil;
}
NSString *OMMARootfsPath(NSString *path) {
    const char *resolved = rootfs(path.fileSystemRepresentation);
    return resolved ? [NSString stringWithUTF8String:resolved] : nil;
}
