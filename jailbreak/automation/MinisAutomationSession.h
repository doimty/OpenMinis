#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>

FOUNDATION_EXPORT double OMMANow(void);
FOUNDATION_EXPORT void OMMASessionInitialize(void);
FOUNDATION_EXPORT BOOL OMMASessionStart(void);
FOUNDATION_EXPORT void OMMASessionStop(void);
FOUNDATION_EXPORT BOOL OMMASessionActive(void);
FOUNDATION_EXPORT BOOL OMMAAuthorize(NSString *authorization, int socket);
FOUNDATION_EXPORT int OMMACurrentSocket(void);
FOUNDATION_EXPORT uint64_t OMMARequestBegin(BOOL interactive, int socket);
FOUNDATION_EXPORT void OMMARequestEnd(uint64_t ticket);
FOUNDATION_EXPORT BOOL OMMARequestAlive(void);
FOUNDATION_EXPORT int OMMAInteractionSleep(unsigned int microseconds);
FOUNDATION_EXPORT void OMMADispatchAsync(dispatch_queue_t queue, dispatch_block_t block);
FOUNDATION_EXPORT void OMMADispatchSync(dispatch_queue_t queue, dispatch_block_t block);
FOUNDATION_EXPORT BOOL OMMAToolAllowed(NSString *name);
FOUNDATION_EXPORT BOOL OMMAToolInteractive(NSString *name);
FOUNDATION_EXPORT NSDictionary *OMMAProvenance(void);
FOUNDATION_EXPORT NSDictionary *OMMATraceReply(NSDictionary *reply);
FOUNDATION_EXPORT NSString *OMMARootPath(NSString *path);
FOUNDATION_EXPORT NSString *OMMARootfsPath(NSString *path);

// Pure production parser is also exercised by the Foundation-only Actions test.
FOUNDATION_EXPORT NSDictionary *OMMAParseSession(NSData *data, double boot, double now,
                                                NSString *commit, NSString *run);
#if OMMA_SESSION_TESTS
FOUNDATION_EXPORT BOOL OMMATestAccept(NSData *data, double boot, double now);
FOUNDATION_EXPORT void OMMATestSetNow(double now);
FOUNDATION_EXPORT NSData *OMMATestReadControl(const char *path, uid_t owner);
#endif
