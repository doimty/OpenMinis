#ifndef DEVICE_MCP_NATIVE_BRIDGE_H
#define DEVICE_MCP_NATIVE_BRIDGE_H

#import <Foundation/Foundation.h>
#include <stdint.h>

NS_ASSUME_NONNULL_BEGIN

/*
 * Narrow Objective-C declaration for the Swift-owned bridge. Keeping this
 * separate from Minis-Swift.h prevents the native offload translation unit
 * from importing every Swift type in the app.
 */
@interface DeviceMCPNativeBridge : NSObject

+ (BOOL)available;
+ (NSDictionary<NSString *, id> *)request:(NSString *)identifier
                                operation:(NSString *)operation
                                    value:(nullable NSString *)value
                                    epoch:(uint64_t)epoch;

@end

NS_ASSUME_NONNULL_END

#endif /* DEVICE_MCP_NATIVE_BRIDGE_H */
