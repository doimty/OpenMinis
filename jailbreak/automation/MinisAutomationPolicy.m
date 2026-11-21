#import "MinisAutomationPolicy.h"
#import <CoreFoundation/CoreFoundation.h>
#include <math.h>

static BOOL omma_value(id value, NSDictionary *schema, NSUInteger depth) {
    if (depth > 8) return NO;
    NSString *type = schema[@"type"];
    if ([type isEqual:@"object"]) {
        if (![value isKindOfClass:NSDictionary.class] || [value count] > 32) return NO;
        NSDictionary *properties = schema[@"properties"] ?: @{};
        for (NSString *required in schema[@"required"] ?: @[]) if (!value[required]) return NO;
        for (id key in value) {
            if (![key isKindOfClass:NSString.class] || !properties[key] || !omma_value(value[key], properties[key], depth + 1)) return NO;
        }
        return YES;
    }
    if ([type isEqual:@"array"]) {
        if (![value isKindOfClass:NSArray.class] || [value count] > 128) return NO;
        for (id element in value) if (!omma_value(element, schema[@"items"] ?: @{}, depth + 1)) return NO;
        return YES;
    }
    if ([type isEqual:@"string"]) return [value isKindOfClass:NSString.class] && [value lengthOfBytesUsingEncoding:NSUTF8StringEncoding] <= 4096;
    BOOL numeric = [value isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)value) != CFBooleanGetTypeID();
    if ([type isEqual:@"boolean"]) return [value isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID();
    if ([type isEqual:@"number"] || [type isEqual:@"integer"]) {
        double number = numeric ? [value doubleValue] : NAN;
        return numeric && isfinite(number) && number >= 0 && number <= 32768
            && (![type isEqual:@"integer"] || floor(number) == number);
    }
    return NO;
}

BOOL OMMAArgumentsValid(NSDictionary *schema, NSDictionary *arguments) {
    if (!omma_value(arguments, schema, 0)) return NO;
    // Keep each synthetic interaction bounded even if an upstream schema only
    // describes its default rather than declaring JSON-Schema maxima.
    for (NSString *key in @[@"duration", @"hold_duration", @"move_duration", @"interval", @"timeout_ms"])
        if ([arguments[key] doubleValue] > 30000) return NO;
    for (NSString *key in @[@"steps", @"index", @"max_elements"])
        if ([arguments[key] doubleValue] > 500) return NO;
    if ([arguments[@"delay_ms"] doubleValue] > 200) return NO;
    return YES;
}

BOOL OMMAHostProtected(NSString *bundle) {
    if (![bundle isKindOfClass:NSString.class] || bundle.length == 0) return YES;
    for (NSString *host in @[@"com.apple.springboard", @"com.apple.backboardd", @"com.apple.runningboardd",
                            @"com.openminis.app", @"com.openminis.app.root"])
        if ([bundle isEqualToString:host] || [bundle hasPrefix:[host stringByAppendingString:@"."]]) return YES;
    return NO;
}
