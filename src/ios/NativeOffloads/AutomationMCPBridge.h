#ifndef AUTOMATION_MCP_BRIDGE_H
#define AUTOMATION_MCP_BRIDGE_H

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/*
 * Narrow Objective-C declaration for the Swift-owned bridge. The native
 * transport only needs this one read-only accessor.
 */
@interface AutomationMCPBridge : NSObject

+ (NSString *)transport;

@end

NS_ASSUME_NONNULL_END

#endif /* AUTOMATION_MCP_BRIDGE_H */
