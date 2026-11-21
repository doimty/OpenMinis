#!/usr/bin/env python3
"""Verify pristine vendor bytes; create namespaced, bounded companion sources."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
UPSTREAM = "62c4464f688c8f269419924b1a19c996de75f175"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def once(source, before, after):
    if source.count(before) != 1:
        raise ValueError(f"pinned source patch does not match exactly: {before[:100]}")
    return source.replace(before, after, 1)


CALL_WRAPPER = r'''
- (NSDictionary *)handleToolsCall:(id)reqId params:(NSDictionary *)params {
    NSString *name = [params[@"name"] isKindOfClass:NSString.class] ? params[@"name"] : nil;
    NSDictionary *args = [params[@"arguments"] isKindOfClass:NSDictionary.class] ? params[@"arguments"] : @{};
    if (!OMMAToolAllowed(name) || (params[@"arguments"] && ![params[@"arguments"] isKindOfClass:NSDictionary.class])
        || ![[NSSet setWithArray:params.allKeys] isSubsetOfSet:[NSSet setWithArray:@[@"name", @"arguments"]]])
        return OMMATraceReply([self mcpError:reqId code:-32602 message:@"Tool or arguments are outside the authorized automation interface."]);
    NSDictionary *schema = nil;
    for (NSDictionary *tool in [self handleToolsList:nil][@"result"][@"tools"])
        if ([tool[@"name"] isEqual:name]) { schema = tool[@"inputSchema"]; break; }
    if (!schema || !OMMAArgumentsValid(schema, args))
        return OMMATraceReply([self mcpError:reqId code:-32602 message:@"Invalid or oversized automation arguments."]);
    if ([name isEqual:@"kill_app"] && OMMAHostProtected(args[@"bundle_id"]))
        return OMMATraceReply([self mcpError:reqId code:-32602 message:@"The automation host cannot be terminated."]);
    uint64_t ticket = OMMARequestBegin(OMMAToolInteractive(name), OMMACurrentSocket());
    if (!ticket) return OMMATraceReply([self mcpError:reqId code:-32001 message:@"Automation session expired, revoked or busy."]);
    @try {
        if (!OMMARequestAlive()) return OMMATraceReply([self mcpSuccess:reqId text:@"Automation request is no longer active." isError:YES]);
        if (OMMAToolInteractive(name)) {
            NSDictionary *state = [[ScreenManager sharedInstance] deviceInteractionState];
            if (![state[@"locked_known"] boolValue] || ![state[@"screen_on_known"] boolValue]
                || [state[@"locked"] boolValue] || ![state[@"screen_on"] boolValue]
                || [state[@"lock_screen_visible"] boolValue])
                return OMMATraceReply([self mcpSuccess:reqId text:@"Unlock and wake the device manually before automation." isError:YES]);
        }
        NSDictionary *reply = [self ommaExecuteAllowedTool:reqId params:params];
        if (!OMMARequestAlive())
            return OMMATraceReply([self mcpSuccess:reqId text:@"Authorization ended during the request; an already sent interaction may have occurred. Inspect the screen before deciding what to do next." isError:YES]);
        return OMMATraceReply(reply);
    } @finally { OMMARequestEnd(ticket); }
}
'''


def patch_server(source):
    source = once(source, '#define MCP_SERVER_NAME             @"ios-mcp"', '#define MCP_SERVER_NAME             @"minis-automation"')
    source = once(source, 'addr.sin_addr.s_addr = INADDR_ANY;', 'addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);')
    source = once(source,
        'Input text into the focused text field through system keyboard events (fast, bulk input). If this fails or times out, retry once with type_text using the same text.',
        'Enter text through Unicode keyboard events. After a failed, expired or disconnected request, inspect the screen before deciding whether any text should be sent again.')
    source = once(source,
        'Type text character-by-character through system keyboard text events, with HID fallback for ASCII keyboard characters. Use this as the fallback when input_text fails.',
        'Type text character by character through Unicode keyboard events. No automatic HID fallback or retry is performed; verify the screen after any uncertain result.')
    source = once(source,
        'Press the home button. On the Lock Screen, one Home press is not enough to assume the device reached the Home screen; use wake_and_home or verify with screenshot/get_ui_elements.',
        'Press Home on an already awake, unlocked device. Verify the resulting screen with screenshot or get_ui_elements. Wake and unlock the device manually if needed.')
    source = once(source, '    _lifecycleState = MCPServerLifecycleStateStarting;\n',
                  '    if (!OMMASessionActive() || port != 8091) { _desiredRunning = NO; return; }\n'
                  '    _lifecycleState = MCPServerLifecycleStateStarting;\n')
    source = once(source, '    NSString *basePath = MCPBasePath(path);',
                  '    NSString *basePath = MCPBasePath(path);\n'
                  '    if (![basePath isEqualToString:@"/mcp"] || !OMMAAuthorize(headers[@"authorization"], clientSocket)) {\n'
                  '        [self sendErrorResponse:clientSocket status:401 message:@"Automation authorization is required." requestLogId:requestLogId];\n'
                  '        free(buffer); return;\n    }')
    source = once(source, '        case 400: statusText = @"Bad Request"; break;',
                  '        case 401: statusText = @"Unauthorized"; break;\n        case 400: statusText = @"Bad Request"; break;')
    lines = source.splitlines()
    instructions = [i for i, line in enumerate(lines) if '@"instructions":' in line]
    if len(instructions) != 1:
        raise ValueError("expected one upstream instructions field")
    lines[instructions[0]] = '            @"instructions": @"Minis Automation controls this user-authorized device for a limited session. Observe with get_frontmost_app, get_screen_info, get_ui_elements and screenshot before interacting. Screenshot image pixels and touch coordinates are screen points; never divide by Retina scale. Never retry an uncertain tap or text input automatically. For locked, screen-off, unavailable or expired status ask the user to wake/unlock or renew in Minis Root. No shell, files, installation or remote service is exposed."'
    source = "\n".join(lines) + "\n"
    source = once(source, '        @"result": @{@"tools": tools}',
                  '        @"result": @{@"tools": [tools filteredArrayUsingPredicate:[NSPredicate predicateWithBlock:^BOOL(NSDictionary *tool, NSDictionary *bindings) { (void)bindings; return OMMAToolAllowed(tool[@"name"]); }]]}')
    source = once(source, '            @"_meta": @{\n                @"protocolCompatibility": @{',
                  '            @"_meta": @{\n                @"openminis": OMMAProvenance(),\n                @"protocolCompatibility": @{')
    source = once(source, '- (NSDictionary *)handleToolsCall:(id)reqId params:(NSDictionary *)params {',
                  CALL_WRAPPER + '\n- (NSDictionary *)ommaExecuteAllowedTool:(id)reqId params:(NSDictionary *)params {')
    source = once(source, '    while (1) {\n        NSDictionary *payload = [self fetchCompactElementsClickableOnly:NO];',
                  '    while (1) {\n        if (!OMMARequestAlive()) return [self mcpError:reqId code:-32001 message:@"Automation wait was revoked."];\n'
                  '        NSDictionary *payload = [self fetchCompactElementsClickableOnly:NO];')
    return source


def patch_hid(source):
    source = once(source, '- (BOOL)sendKeyboardEventWithPage:(uint32_t)page usage:(uint32_t)usage down:(BOOL)down error:(NSString **)error {',
                  '- (BOOL)sendKeyboardEventWithPage:(uint32_t)page usage:(uint32_t)usage down:(BOOL)down error:(NSString **)error {\n'
                  '    if (down && !OMMARequestAlive()) { if (error) *error = @"Automation input was revoked."; return NO; }')
    source = once(source, '- (BOOL)performTouchAtPoint:(CGPoint)point phase:(TouchPhase)phase error:(NSString **)error {',
                  '- (BOOL)performTouchAtPoint:(CGPoint)point phase:(TouchPhase)phase error:(NSString **)error {\n'
                  '    if (phase != TouchPhaseEnded && !OMMARequestAlive()) {\n'
                  '        [self performTouchAtPoint:point phase:TouchPhaseEnded error:NULL];\n'
                  '        if (error) *error = @"Automation gesture was revoked."; return NO;\n    }')
    return source


def patch_text(source):
    source = once(source, '    for (NSUInteger i = 0; i < text.length; i++) {\n        unichar ch = [text characterAtIndex:i];\n        KeyMapping mapping',
                  '    for (NSUInteger i = 0; i < text.length; i++) {\n        if (!OMMARequestAlive()) return NO;\n'
                  '        unichar ch = [text characterAtIndex:i];\n        KeyMapping mapping')
    source = once(source, '- (BOOL)dispatchUnicodeTextChunk:(NSString *)chunk targetPid:(pid_t)targetPid error:(NSString **)error {',
                  '- (BOOL)dispatchUnicodeTextChunk:(NSString *)chunk targetPid:(pid_t)targetPid error:(NSString **)error {\n'
                  '    if (!OMMARequestAlive()) { if (error) *error = @"Automation text input was revoked."; return NO; }')
    source = once(source, '- (void)sendKeyEvent:(uint32_t)usagePage usage:(uint32_t)usage down:(BOOL)down {',
                  '- (void)sendKeyEvent:(uint32_t)usagePage usage:(uint32_t)usage down:(BOOL)down {\n'
                  '    if (down && !OMMARequestAlive()) return;')
    for delay in ("10", "delayMs"):
        fallback = f'''        if ([self typeTextViaHID:text delayMs:{delay}]) {{
            if (completion) completion(YES, @"Used HID fallback for ASCII text");
            return;
        }}
'''
        source = once(source, fallback, '        // No automatic fallback: failed Unicode input may already have sent text.\n')
    return source


def patch_apps(source):
    source = once(source, '- (BOOL)launchApp:(NSString *)bundleId error:(NSString **)error {',
                  '- (BOOL)launchApp:(NSString *)bundleId error:(NSString **)error {\n'
                  '    if (!OMMARequestAlive()) { if (error) *error = @"Automation session ended."; return NO; }')
    source = once(source, '                    BOOL opened = ((BOOL (*)(id, SEL, NSString *))objc_msgSend)(workspace, openSel, bundleId);',
                  '                    if (!OMMARequestAlive()) { if (error) *error = @"Automation session ended."; return NO; }\n'
                  '                    BOOL opened = ((BOOL (*)(id, SEL, NSString *))objc_msgSend)(workspace, openSel, bundleId);')
    source = once(source, '    dispatch_block_t fallbackBlock = ^{',
                  '    dispatch_block_t fallbackBlock = ^{\n        if (!OMMARequestAlive()) { errMsg = @"Automation session ended."; return; }')
    source = once(source, '- (BOOL)killApp:(NSString *)bundleId error:(NSString **)error {',
                  '- (BOOL)killApp:(NSString *)bundleId error:(NSString **)error {\n'
                  '    if (!OMMARequestAlive() || OMMAHostProtected(bundleId)) { if (error) *error = @"Protected host or expired session."; return NO; }')
    source = once(source, '            [inv invoke];\n\n            APP_LOG(@"Killed app:',
                  '            if (!OMMARequestAlive()) { errMsg = @"Automation session ended."; return; }\n'
                  '            [inv invoke];\n\n            APP_LOG(@"Killed app:')
    return source


def prepare(output):
    vendor = ROOT / "vendor/ios-mcp"
    lock = json.loads((vendor / "source.json").read_text(encoding="utf-8"))
    if lock["commit"] != UPSTREAM:
        raise ValueError("unexpected upstream revision")
    roothide = ROOT / "vendor/libroothide"
    roothide_lock = json.loads((roothide / "source.json").read_text(encoding="utf-8"))
    if roothide_lock["commit"] != "cb236f9cf1c5e2ac932611328998a62b4b133e33":
        raise ValueError("unexpected RootHide ABI revision")
    for name, digest in roothide_lock["files"].items():
        if sha(roothide / name) != digest:
            raise ValueError(f"modified RootHide public ABI: {name}")
    texts = {}
    for name, digest in lock["files"].items():
        path = vendor / name
        if sha(path) != digest:
            raise ValueError(f"modified vendor source: {name}")
        if path.suffix in (".h", ".m"):
            texts[name] = path.read_text(encoding="utf-8")
    texts["MCPServer.m"] = patch_server(texts["MCPServer.m"])
    texts["HIDManager.m"] = patch_hid(texts["HIDManager.m"])
    texts["TextInputManager.m"] = patch_text(texts["TextInputManager.m"])
    texts["AppManager.m"] = patch_apps(texts["AppManager.m"])
    for name in ("HIDManager.m", "TextInputManager.m"):
        texts[name] = texts[name].replace("usleep(", "OMMAInteractionSleep(")
        texts[name] = texts[name].replace("OMMARequestAlive()", "OMMAInteractionAlive()")
        texts[name] = '#import "MinisAutomationInteraction.h"\n' + texts[name]
    texts["AppManager.m"] = texts["AppManager.m"].replace("OMMARequestAlive()", "OMMAInteractionAlive()")
    texts["AppManager.m"] = '#import "MinisAutomationInteraction.h"\n' + texts["AppManager.m"]
    # The upstream logger can include error strings derived from user input.
    # The companion's metadata receipt is its audit surface; disable debug
    # preference logging rather than silently inheriting another setting.
    logger = texts["MCPLogger.m"]
    start = logger.index("+ (BOOL)isDebugLoggingEnabled {")
    end = logger.index("\n+ (NSString *)logDirectoryPath", start)
    texts["MCPLogger.m"] = logger[:start] + "+ (BOOL)isDebugLoggingEnabled { return NO; }\n" + logger[end:]
    texts["IOSMCPPreferences.h"] = texts["IOSMCPPreferences.h"].replace("com.witchan.ios-mcp", "com.openminis.automation")
    for path in ROOT.glob("MinisAutomation*.[hm]"):
        texts[path.name] = path.read_text(encoding="utf-8")
    classes = set()
    for source in texts.values():
        classes.update(re.findall(r"@implementation\s+(\w+)", source))
    identifier = re.compile(r"\b(?:MCP[A-Za-z0-9_]+|IOSMCP[A-Za-z0-9_]+|" + "|".join(sorted(classes)) + r")\b")
    def namespace(source):
        return identifier.sub(lambda m: m.group(0) if m.group(0) in {"MCP_ROOTLESS", "MCP_ROOTHIDE"} or m.group(0).startswith("OMMA")
                              else "OMMAV_" + m.group(0), source)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("generated source directory must be empty")
    generated = {}
    for name, source in texts.items():
        if name in ("AppManager.m", "MCPProcessUtil.m"):
            source = source.replace('#include <roothide.h>', '')
            source = source.replace('jbroot(path)', 'OMMARootPath(path)').replace('rootfs(path)', 'OMMARootfsPath(path)')
        if name.endswith(".m") and not name.startswith("MinisAutomation"):
            source = '#import "MinisAutomationSession.h"\n#import "MinisAutomationPolicy.h"\n' + source
            source = source.replace('dispatch_async(', 'OMMADispatchAsync(').replace('dispatch_sync(', 'OMMADispatchSync(')
        path = output / namespace(name)
        path.write_text(namespace(source), encoding="utf-8", newline="\n")
        generated[path.name] = sha(path)
    result = {"schema": 1, "upstream_repository": lock["repository"], "upstream_commit": UPSTREAM,
              "upstream_source_hashes": lock["files"], "generated_source_hashes": generated,
              "roothide_abi": roothide_lock,
              "local_source_hashes": {p.name: sha(p) for p in ROOT.iterdir() if p.is_file() and p.suffix in (".py", ".m", ".h", ".sh")},
              "patch_script_sha256": sha(Path(__file__)), "external_binaries_included": False}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.output)
    args.lock.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
