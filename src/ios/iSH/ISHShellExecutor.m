//
//  ISHShellExecutor.m
//  MinisApp
//
//  Shell execution implementation with line-by-line output and process completion
//

#import "ISHShellExecutor.h"
#import "ISHKernel.h"

#include <poll.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#import "ISHExecutionIdentity.h"
#include <libkern/OSAtomic.h>
#include "ish/kernel/init.h"
#include "ish/kernel/calls.h"
#include "ish/kernel/task.h"
#include "ish/kernel/signal.h"
#include "ish/kernel/fs.h"
#include "ish/fs/devices.h"
#include "ish/fs/real.h"

#pragma mark - Leak Guard Tunables

/// Hard cap on how long a single reader thread may live.
///
/// Not a command timeout — callers set their own, and this is far above any of
/// them. It only bounds the damage from a reader that never observed the
/// signal to stop, so a stranded thread cannot hold a concurrent-queue slot
/// for the life of the process.
static const NSTimeInterval ISHShellExecutorReaderMaxLifetime = 3600.0; // 1h

/// How often the stale-context sweeper runs.
static const NSTimeInterval ISHShellExecutorSweepInterval = 60.0;

/// How long past its last sign of life a context may sit in _activeExecutions
/// before the sweeper reclaims it. Must comfortably exceed the longest command
/// anyone runs, because the sweeper cannot distinguish "still working" from
/// "stranded" — it only sees an entry that has been there a long time.
static const NSTimeInterval ISHShellExecutorStaleContextAge = 7200.0; // 2h

#pragma mark - Result Implementation

@interface ISHShellExecutionResult ()
@property (nonatomic, readwrite) int exitCode;
@property (nonatomic, readwrite) int pid;
@property (nonatomic, readwrite) ISHShellExecutorError error;
@property (nonatomic, readwrite, copy) NSString *output;
@property (nonatomic, readwrite, copy) NSString *errorOutput;
@property (nonatomic, readwrite) NSTimeInterval duration;
@end

@implementation ISHShellExecutionResult
@end

#pragma mark - Execution Context

@class ISHShellExecutionContext;

@interface ISHShellExecutionTicket ()
@property (nonatomic, readwrite) uint64_t token;
@property (nonatomic, strong) ISHShellExecutionContext *context; // guarded by ticket
@property (nonatomic) BOOL launchClaimed;
@property (nonatomic) BOOL terminationRequested;
@property (nonatomic) ISHShellExecutorError terminationError;
@property (nonatomic) dispatch_semaphore_t waitSemaphore;
@property (nonatomic, strong) ISHShellExecutionResult *completedResult;
- (instancetype)initWithToken:(uint64_t)token;
@end

@implementation ISHShellExecutionTicket
- (instancetype)initWithToken:(uint64_t)token {
    if ((self = [super init])) _token = token;
    return self;
}
@end

@interface ISHShellExecutionContext : NSObject {
    int _stdoutPipe[2];
    int _stderrPipe[2];
    /// Reader count. A plain int32 driven by OSAtomic rather than an `atomic`
    /// property: `self.liveReaders--` on an atomic property is still a
    /// read-modify-write with a gap between the load and the store, so two
    /// readers finishing at once can both read N and both store N-1. Losing a
    /// decrement leaves the count permanently above zero and the pipes never
    /// close; losing it the other way closes them while a reader still polls.
    int32_t _liveReaders;
}
@property (nonatomic) int guestPid;
@property (nonatomic) uint64_t ticketToken;
// ticket owns context while live; this direction MUST stay weak.
@property (nonatomic, weak) ISHShellExecutionTicket *ticket;
@property (nonatomic) NSDate *startTime;
@property (nonatomic, copy) ISHShellLineCallback lineCallback;
@property (nonatomic, copy) ISHShellCompletionCallback completion;
@property (nonatomic) NSMutableString *stdoutBuffer;
@property (nonatomic) NSMutableString *stderrBuffer;
@property (nonatomic) dispatch_semaphore_t waitSemaphore;
@property (nonatomic) ISHShellExecutionResult *result;
@property (atomic) BOOL isCompleted;
/// Readers still running for this context. Incremented before each reader is
/// dispatched and decremented as it returns, so `cleanup` can tell whether the
/// pipe fds are still being polled. Mutated only via retainReader/releaseReader.
@property (readonly) int32_t liveReaders;
- (void)retainReader;
/// Returns the count remaining after this reader leaves.
- (int32_t)releaseReader;
/// Set once the run has been finalised (by exit OR by timeout), so the two
/// paths cannot both finalise the same context. See +finalizeContext:.
@property (atomic) BOOL didFinalize;

- (int *)stdoutPipe;
- (int *)stderrPipe;
- (BOOL)closePipesIfNoReaders;

@end

@implementation ISHShellExecutionContext

- (int *)stdoutPipe {
    return _stdoutPipe;
}

- (int *)stderrPipe {
    return _stderrPipe;
}

- (instancetype)init {
    if (self = [super init]) {
        _stdoutBuffer = [NSMutableString string];
        _stderrBuffer = [NSMutableString string];
        _stdoutPipe[0] = -1;
        _stdoutPipe[1] = -1;
        _stderrPipe[0] = -1;
        _stderrPipe[1] = -1;
        _result = [[ISHShellExecutionResult alloc] init];
        _result.error = ISHShellExecutorErrorNone;
    }
    return self;
}

/// Close the pipe fds, but ONLY once no reader thread is still polling them.
/// Returns YES if the fds were closed (or were already closed).
///
/// Closing an fd that another thread is blocked in poll() on is a
/// use-after-free of the descriptor number: poll does not fail, the number is
/// immediately reusable, and the next pipe()/open() in the process hands the
/// same number to someone else — after which this reader is reading a
/// DIFFERENT command's pipe and appending that output to this command's
/// buffer. So the close is gated on the reader count reaching zero; readers
/// are told to stop by `isCompleted`, and whoever observes the last one leave
/// performs the close.
///
/// The fd array is guarded by @synchronized(self) because the closing thread
/// and the last reader can reach here at the same time; without it both can
/// see a non-negative fd and close it twice, and a double close is just as
/// dangerous as an early one (it can close an unrelated, freshly-recycled fd).
- (int32_t)liveReaders {
    return OSAtomicAdd32(0, &_liveReaders);   // atomic load
}

- (void)retainReader {
    OSAtomicIncrement32(&_liveReaders);
}

- (int32_t)releaseReader {
    return OSAtomicDecrement32(&_liveReaders);
}

- (BOOL)closePipesIfNoReaders {
    if (self.liveReaders > 0) return NO;
    @synchronized(self) {
        if (_stdoutPipe[0] >= 0) close(_stdoutPipe[0]);
        if (_stdoutPipe[1] >= 0) close(_stdoutPipe[1]);
        if (_stderrPipe[0] >= 0) close(_stderrPipe[0]);
        if (_stderrPipe[1] >= 0) close(_stderrPipe[1]);
        _stdoutPipe[0] = _stdoutPipe[1] = -1;
        _stderrPipe[0] = _stderrPipe[1] = -1;
    }
    return YES;
}

/// Ask readers to stop, then close if they already have.
///
/// Kept as `cleanup` because existing call sites use it, but note the two
/// distinct cases: on the failure paths in the launch routine no reader has
/// been started yet (liveReaders == 0), so this closes immediately, which is
/// what those paths need. Once readers are running the close is deferred to
/// whichever reader exits last.
- (void)cleanup {
    self.isCompleted = YES;
    [self closePipesIfNoReaders];
}

- (void)dealloc {
    // Last resort. If a reader were somehow still running, `self` could not be
    // deallocating — the reader block retains the context — so by definition
    // liveReaders is 0 here and closing is safe.
    [self closePipesIfNoReaders];
}

@end

#pragma mark - Identity-scoped, bounded cleanup

// Returns NO immediately when pids_lock is busy. A successful pass scans every
// legal non-init PID slot (2..<MAX_PID); no root/pgid/parent/task-address
// identity is consulted.
// Remaining boundary: send_signal itself can wait on internal kernel locks.
// Only the single serial cleanup worker may reach that boundary.
static BOOL ISHSignalTasksForExecutionToken(uint64_t token, int signal) {
    if (!ISHExecutionIdentityIsValid(token)) return YES;
    if (trylock(&pids_lock) != 0) return NO;
    struct siginfo_ info = SIGINFO_NIL;
    for (int pid = 2; pid < MAX_PID; pid++) {
        struct task *task = pid_get_task((dword_t)pid);
        if (task != NULL && task->group != NULL &&
            ISHExecutionIdentityEqual(token, task->group->fs_context)) {
            send_signal(task, signal, info);
        }
    }
    unlock(&pids_lock);
    return YES;
}

// task_run_current checks doing_group_exit before touching guest memory/JIT,
// so cancellation after task creation can release through real do_exit without
// ever executing the command. pthread_create failure remains a kernel boundary.
static int ISHStartCancelledTask(struct task *task, uint64_t token) {
    if (task == NULL) return -1;
    lock(&pids_lock);
    BOOL owned = task->group != NULL &&
        ISHExecutionIdentityEqual(token, task->group->fs_context);
    if (owned) {
        task->group->group_exit_code = SIGKILL_;
        task->group->doing_group_exit = true;
    }
    unlock(&pids_lock);
    if (!owned) return -1;
    return task_start(task);
}

@interface ISHShellCleanupRequest : NSObject
@property (nonatomic) uint64_t token;
@property (nonatomic) NSUInteger revision; // guarded by cleanup registry
@property (nonatomic) NSUInteger passRevision;
@property (nonatomic) NSUInteger attempts;
@property (nonatomic) BOOL killPhase;
@end
@implementation ISHShellCleanupRequest
@end

#pragma mark - Executor Implementation

@implementation ISHShellExecutor

static NSMutableDictionary<NSNumber *, ISHShellExecutionContext *> *_activeExecutions;
static dispatch_queue_t _readerQueue;
static dispatch_once_t _onceToken;
static dispatch_source_t _sweepTimer;
static _Atomic(uint64_t) _ticketGeneration = 0;
static dispatch_queue_t _cleanupQueue;
static NSMutableDictionary<NSNumber *, ISHShellCleanupRequest *> *_cleanupRequests;
static const NSUInteger ISHShellCleanupCapacity = 64;
static const NSUInteger ISHShellCleanupMaxAttempts = 20;

/// Live reader threads across all executions. The leak this file exists to
/// prevent shows up here as a number that only ever goes up: each stranded
/// command pins two readers on a DISPATCH_QUEUE_CONCURRENT queue, and once
/// GCD's worker pool is exhausted no new command can run at all.
static int32_t _globalLiveReaders = 0;
/// How many contexts the sweeper has had to reclaim. Non-zero means a run
/// finished without either the exit notification or the timeout path
/// finalising it — i.e. a bug upstream of the sweeper, worth seeing in logs.
static int32_t _sweptContexts = 0;

+ (void)initialize {
    if (self == [ISHShellExecutor class]) {
        _activeExecutions = [NSMutableDictionary dictionary];
        _cleanupRequests = [NSMutableDictionary dictionary];
        _cleanupQueue = dispatch_queue_create("com.ish.shellexecutor.cleanup", DISPATCH_QUEUE_SERIAL);
        dispatch_once(&_onceToken, ^{
            _readerQueue = dispatch_queue_create("com.ish.shellexecutor.reader",
                                                 DISPATCH_QUEUE_CONCURRENT);
        });

        // Register for process exit notifications
        [[NSNotificationCenter defaultCenter] addObserver:self
                                                 selector:@selector(processDidExit:)
                                                     name:ISHProcessExitedNotification
                                                   object:nil];

        [self startStaleContextSweeper];
    }
}

#pragma mark - Stale Context Sweeper

/// Periodically reclaim contexts that were never finalised.
///
/// The timeout path and the exit notification between them should finalise
/// every run, so in a correct build this never fires. It is here because the
/// failure it guards against is not proportionate: one missed teardown pins
/// two reader threads for the life of the process, and enough of them make the
/// app unable to run any command until the device is restarted. A periodic
/// check is cheap; that outcome is not.
///
/// Anything it reclaims is a bug upstream, so it logs at a level meant to be
/// noticed rather than cleaning up quietly.
+ (void)startStaleContextSweeper {
    _sweepTimer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0,
                                         dispatch_get_global_queue(QOS_CLASS_UTILITY, 0));
    dispatch_source_set_timer(_sweepTimer,
                              dispatch_time(DISPATCH_TIME_NOW,
                                            (int64_t)(ISHShellExecutorSweepInterval * NSEC_PER_SEC)),
                              (uint64_t)(ISHShellExecutorSweepInterval * NSEC_PER_SEC),
                              (uint64_t)(5 * NSEC_PER_SEC));   // generous leeway: not time-critical
    dispatch_source_set_event_handler(_sweepTimer, ^{
        [self sweepStaleContexts];
    });
    dispatch_resume(_sweepTimer);
}

+ (void)sweepStaleContexts {
    NSMutableArray<ISHShellExecutionContext *> *stale = [NSMutableArray array];

    // Collect under the lock, finalise outside it. finalizeContext: invokes
    // the caller's completion block, and running arbitrary caller code while
    // holding the registry lock is how a deadlock gets introduced — that block
    // is free to start another command.
    @synchronized(_activeExecutions) {
        for (NSNumber *key in _activeExecutions) {
            ISHShellExecutionContext *ctx = _activeExecutions[key];
            NSTimeInterval age = -[ctx.startTime timeIntervalSinceNow];
            if (age > ISHShellExecutorStaleContextAge) {
                [stale addObject:ctx];
            }
        }
    }

    if (stale.count == 0) return;

    NSLog(@"ISHShellExecutor[sweep]: reclaiming %lu stale context(s) — these should "
          @"have been finalised by exit or timeout; liveReaders=%d",
          (unsigned long)stale.count, _globalLiveReaders);

    for (ISHShellExecutionContext *ctx in stale) {
        NSLog(@"ISHShellExecutor[sweep]: pid=%d age=%.0fs isCompleted=%d readers=%d",
              ctx.guestPid, -[ctx.startTime timeIntervalSinceNow],
              ctx.isCompleted, ctx.liveReaders);
        OSAtomicIncrement32(&_sweptContexts);
        [self finalizeContext:ctx exitCode:-1 error:ISHShellExecutorErrorTimeout];
    }
}

/// Reader/leak counters, for diagnosing this class of problem from a log.
+ (NSString *)leakGuardStatus {
    NSUInteger active;
    @synchronized(_activeExecutions) { active = _activeExecutions.count; }
    return [NSString stringWithFormat:
            @"activeExecutions=%lu liveReaders=%d sweptContexts=%d",
            (unsigned long)active, _globalLiveReaders, _sweptContexts];
}

#pragma mark - Ticket lifecycle and cleanup queue

+ (ISHShellExecutionTicket *)makeTicketWithFSContext:(uint64_t)context {
    uint64_t token = 0;
    if (!ISHExecutionIdentityNext(&_ticketGeneration, context, &token)) return nil;
    return [[ISHShellExecutionTicket alloc] initWithToken:token];
}

+ (void)enqueueCleanupForToken:(uint64_t)token {
    if (!ISHExecutionIdentityIsValid(token)) return;
    ISHShellCleanupRequest *request;
    @synchronized(_cleanupRequests) {
        request = _cleanupRequests[@(token)];
        if (request) {
            // A launch finishing after an earlier cleanup requires another
            // full pass, even if that earlier pass has already scanned it.
            request.revision++;
            return;
        }
        if (_cleanupRequests.count >= ISHShellCleanupCapacity) {
            NSLog(@"ISHShellExecutor[cleanup]: capacity reached; token=%llu cleanup not queued",
                  (unsigned long long)token);
            return;
        }
        request = [[ISHShellCleanupRequest alloc] init];
        request.token = token;
        request.revision = request.passRevision = 1;
        _cleanupRequests[@(token)] = request;
    }
    dispatch_async(_cleanupQueue, ^{ [self runCleanupRequest:request]; });
}

+ (void)runCleanupRequest:(ISHShellCleanupRequest *)request {
    BOOL acquired = ISHSignalTasksForExecutionToken(request.token,
                                                   request.killPhase ? SIGKILL_ : SIGTERM_);
    request.attempts++;
    if (!acquired && request.attempts < ISHShellCleanupMaxAttempts) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 25 * NSEC_PER_MSEC),
                       _cleanupQueue, ^{ [self runCleanupRequest:request]; });
        return;
    }
    if (!acquired) {
        NSLog(@"ISHShellExecutor[cleanup]: token=%llu pids_lock busy; phase=%@ exhausted",
              (unsigned long long)request.token, request.killPhase ? @"KILL" : @"TERM");
    }
    if (!request.killPhase) {
        request.killPhase = YES;
        request.attempts = 0;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 100 * NSEC_PER_MSEC),
                       _cleanupQueue, ^{ [self runCleanupRequest:request]; });
        return;
    }
    BOOL repeat = NO;
    @synchronized(_cleanupRequests) {
        if (request.revision != request.passRevision) {
            request.passRevision = request.revision;
            repeat = YES;
        } else if (_cleanupRequests[@(request.token)] == request) {
            [_cleanupRequests removeObjectForKey:@(request.token)];
        }
    }
    if (repeat) {
        request.killPhase = NO;
        request.attempts = 0;
        dispatch_async(_cleanupQueue, ^{ [self runCleanupRequest:request]; });
    }
}

+ (void)requestTerminationForTicket:(ISHShellExecutionTicket *)ticket
                              error:(ISHShellExecutorError)error {
    if (!ticket || !ISHExecutionIdentityIsValid(ticket.token)) return;
    ISHShellExecutionContext *ctx;
    BOOL firstRequest;
    @synchronized(ticket) {
        firstRequest = !ticket.terminationRequested;
        if (firstRequest) {
            ticket.terminationRequested = YES;
            ticket.terminationError = error;
        }
        ctx = ticket.context;
        error = ticket.terminationError;
    }
    // Neither finalization nor caller callbacks run with the registry held.
    [self finalizeContext:ctx exitCode:-1 error:error];
    if (firstRequest) [self enqueueCleanupForToken:ticket.token];
}

#pragma mark - Public API

+ (int)executeCommand:(NSString *)command
         lineCallback:(ISHShellLineCallback)lineCallback
           completion:(ISHShellCompletionCallback)completion {
    // [T-heredoc-trailing-newline] busybox ash's heredoc parser needs a newline
    // after the terminator line to confirm the here-doc is closed. When the
    // command string ends exactly at the terminator with no trailing "\n"
    // (e.g. `python3 <<'EOF'\n…\nEOF`), ash keeps waiting for the terminator,
    // hits end-of-input, and fails with `unexpected end of file (expecting ")")`
    // — a 100% deterministic failure for any heredoc-terminated command.
    // Appending a newline is harmless for every other command (a trailing blank
    // line is a no-op in sh) and fixes the heredoc case. Applies to ALL
    // shell_execute commands since they all funnel through here.
    NSString *normalized = [command hasSuffix:@"\n"] ? command : [command stringByAppendingString:@"\n"];
    return [self executeExecutable:@"/bin/sh"
                         arguments:@[@"-c", normalized]
                       environment:nil
                      lineCallback:lineCallback
                        completion:completion];
}

+ (int)executeExecutable:(NSString *)executable
               arguments:(NSArray<NSString *> *)arguments
             environment:(NSDictionary<NSString *, NSString *> *)environment
            lineCallback:(ISHShellLineCallback)lineCallback
              completion:(ISHShellCompletionCallback)completion {
    return [self executeExecutable:executable
                         arguments:arguments
                       environment:environment
                         stdinData:nil
                      lineCallback:lineCallback
                        completion:completion];
}

+ (int)executeExecutable:(NSString *)executable
               arguments:(NSArray<NSString *> *)arguments
             environment:(NSDictionary<NSString *, NSString *> *)environment
               stdinData:(NSData *)stdinData
            lineCallback:(ISHShellLineCallback)lineCallback
              completion:(ISHShellCompletionCallback)completion {
    return [self executeExecutable:executable
                         arguments:arguments
                       environment:environment
                         stdinData:stdinData
                         fsContext:0
                      lineCallback:lineCallback
                        completion:completion];
}

+ (int)executeExecutable:(NSString *)executable
               arguments:(NSArray<NSString *> *)arguments
             environment:(NSDictionary<NSString *, NSString *> *)environment
               stdinData:(NSData *)stdinData
               fsContext:(uint64_t)fsContext
            lineCallback:(ISHShellLineCallback)lineCallback
              completion:(ISHShellCompletionCallback)completion {
    ISHShellExecutionTicket *ticket = [self makeTicketWithFSContext:fsContext];
    if (!ticket) return ISHShellExecutorErrorProcessCreationFailed;
    return [self executeExecutable:executable arguments:arguments environment:environment
                        stdinData:stdinData executionTicket:ticket
                     lineCallback:lineCallback completion:completion];
}

+ (int)executeExecutable:(NSString *)executable
               arguments:(NSArray<NSString *> *)arguments
             environment:(NSDictionary<NSString *, NSString *> *)environment
               stdinData:(NSData *)stdinData
         executionTicket:(ISHShellExecutionTicket *)ticket
            lineCallback:(ISHShellLineCallback)lineCallback
              completion:(ISHShellCompletionCallback)completion {
    if (!ticket || !ISHExecutionIdentityIsValid(ticket.token))
        return ISHShellExecutorErrorProcessCreationFailed;
    ISHShellExecutionContext *ctx = [[ISHShellExecutionContext alloc] init];
    ctx.lineCallback = lineCallback;
    ctx.completion = completion;
    ctx.startTime = [NSDate date];
    ctx.ticketToken = ticket.token;
    ctx.ticket = ticket;

    // Startup owns every pipe until its @finally, even if cancellation wins
    // while prepare is using those fds. Bind before touching the kernel.
    [ctx retainReader];
    struct task *saved_current = current;
    struct task *preparedTask = NULL;
    int stdinPipe[2] = {-1, -1};
    BOOL launched = NO;
    BOOL taskStartAttempted = NO;
    BOOL attached = NO;
    ISHShellExecutorError failure = ISHShellExecutorErrorProcessCreationFailed;
    @try {
        BOOL cancelled;
        @synchronized(ticket) {
            if (ticket.launchClaimed) return ISHShellExecutorErrorCancelled;
            ticket.launchClaimed = YES;
            cancelled = ticket.terminationRequested;
            if (cancelled) failure = ticket.terminationError;
            ticket.context = ctx;
            ctx.waitSemaphore = ticket.waitSemaphore;
            attached = YES;
        }
        if (cancelled) return ISHShellExecutorErrorCancelled;
        // become_new_init_child asserts when init has not booted.
        if (!ISHKernel.shared.isBooted) {
            NSLog(@"ISHShellExecutor: kernel not booted — refusing to execute %@", executable);
            return ISHShellExecutorErrorProcessCreationFailed;
        }

    // Create pipes for stdout and stderr
    if (pipe([ctx stdoutPipe]) < 0 || pipe([ctx stderrPipe]) < 0) {
        NSLog(@"ISHShellExecutor: pipe() failed: %s", strerror(errno));
        [ctx cleanup];
        return ISHShellExecutorErrorProcessCreationFailed;
    }

    // Set read ends to non-blocking
    fcntl([ctx stdoutPipe][0], F_SETFL, O_NONBLOCK);
    fcntl([ctx stderrPipe][0], F_SETFL, O_NONBLOCK);

    // Cancellation that already won before creation must not allocate a guest.
    @synchronized(ticket) {
        if (ticket.terminationRequested) return ISHShellExecutorErrorCancelled;
    }
    // Create new process
    int err = become_new_init_child();
    if (err < 0) {
        current = saved_current;
        [ctx cleanup];
        NSLog(@"ISHShellExecutor: become_new_init_child failed: %d", err);
        return ISHShellExecutorErrorProcessCreationFailed;
    }

    struct task *task = current;
    preparedTask = task;

    // Every execution, including global route zero, gets a nonzero inherited
    // generation. Missing group means ownership cannot be established safely.
    if (task->group == NULL) return ISHShellExecutorErrorProcessCreationFailed;
    lock(&pids_lock);
    task->group->fs_context = ctx.ticketToken;
    unlock(&pids_lock);
    BOOL cancelledAfterStamp;
    @synchronized(ticket) { cancelledAfterStamp = ticket.terminationRequested; }
    if (cancelledAfterStamp) {
        // @finally starts this owned task exit-only, then repeats cleanup.
        return ISHShellExecutorErrorCancelled;
    }

    // Setup stdin: pipe if stdinData provided, /dev/null otherwise
    if (stdinData) {
        if (pipe(stdinPipe) < 0) {
            NSLog(@"ISHShellExecutor[stdin]: pipe() failed: %s", strerror(errno));
            current = saved_current;
            [ctx cleanup];
            return ISHShellExecutorErrorProcessCreationFailed;
        }
        NSLog(@"ISHShellExecutor[stdin]: pipe created (read=%d, write=%d), stdinData=%lu bytes",
              stdinPipe[0], stdinPipe[1], (unsigned long)stdinData.length);
    } else {
        NSLog(@"ISHShellExecutor[stdin]: no stdinData, will use /dev/null");
    }
    struct fd *stdin_fd = adhoc_fd_create(&realfs_fdops);
    if (stdin_fd) {
        int real_fd = stdinData ? dup(stdinPipe[0]) : open("/dev/null", O_RDONLY);
        if (real_fd < 0) {
            NSLog(@"ISHShellExecutor[stdin]: fd creation failed: %s", strerror(errno));
            if (stdinData) {
                close(stdinPipe[0]); close(stdinPipe[1]);
                stdinPipe[0] = stdinPipe[1] = -1;
            }
            current = saved_current;
            [ctx cleanup];
            return ISHShellExecutorErrorProcessCreationFailed;
        }
        stdin_fd->real_fd = real_fd;
        NSLog(@"ISHShellExecutor[stdin]: fd0 real_fd=%d (source=%s)", stdin_fd->real_fd, stdinData ? "pipe" : "/dev/null");
        task->files->files[0] = stdin_fd;
    }
    if (stdinData) {
        close(stdinPipe[0]);
        stdinPipe[0] = -1;
    }

    // Setup stdout to pipe
    struct fd *stdout_fd = adhoc_fd_create(&realfs_fdops);
    if (stdout_fd) {
        int real_fd = dup([ctx stdoutPipe][1]);
        if (real_fd < 0) {
            NSLog(@"ISHShellExecutor[stdout]: dup() failed: %s", strerror(errno));
            current = saved_current;
            [ctx cleanup];
            return ISHShellExecutorErrorProcessCreationFailed;
        }
        stdout_fd->real_fd = real_fd;
        task->files->files[1] = stdout_fd;
    }

    // Setup stderr to pipe
    struct fd *stderr_fd = adhoc_fd_create(&realfs_fdops);
    if (stderr_fd) {
        int real_fd = dup([ctx stderrPipe][1]);
        if (real_fd < 0) {
            NSLog(@"ISHShellExecutor[stderr]: dup() failed: %s", strerror(errno));
            current = saved_current;
            [ctx cleanup];
            return ISHShellExecutorErrorProcessCreationFailed;
        }
        stderr_fd->real_fd = real_fd;
        task->files->files[2] = stderr_fd;
    }

    // Close write ends in parent
    close([ctx stdoutPipe][1]);
    close([ctx stderrPipe][1]);
    [ctx stdoutPipe][1] = -1;
    [ctx stderrPipe][1] = -1;

    // Detect if we're launching node (for V8 flags injection)
    const char *exec_path = executable.UTF8String;
    const char *base = strrchr(exec_path, '/');
    base = base ? base + 1 : exec_path;
    bool is_node = (strcmp(base, "node") == 0);

    // Build argv (NUL-separated string), with V8 flags injection for node
    char argv_buf[16384];
    size_t pos = 0;
    int exec_argc = 0;

    if (is_node) {
        // Copy argv[0] (executable)
        size_t len = strlen(exec_path) + 1;
        memcpy(argv_buf + pos, exec_path, len);
        pos += len;
        exec_argc++;
        // Inject V8 flags
        static const char *v8_flags[] = {
            "--jitless",
            "--no-lazy",
            "--no-expose-wasm",
            "--max-old-space-size=512",
        };
        for (int fi = 0; fi < (int)(sizeof(v8_flags)/sizeof(v8_flags[0])); fi++) {
            len = strlen(v8_flags[fi]) + 1;
            if (pos + len >= sizeof(argv_buf) - 1) break;
            memcpy(argv_buf + pos, v8_flags[fi], len);
            pos += len;
            exec_argc++;
        }
        // Copy remaining arguments
        for (NSString *arg in arguments) {
            const char *str = arg.UTF8String;
            len = strlen(str) + 1;
            if (pos + len >= sizeof(argv_buf) - 1) break;
            memcpy(argv_buf + pos, str, len);
            pos += len;
            exec_argc++;
        }
    } else {
        NSMutableArray<NSString *> *fullArgs = [NSMutableArray arrayWithObject:executable];
        if (arguments) {
            [fullArgs addObjectsFromArray:arguments];
        }
        for (NSString *arg in fullArgs) {
            const char *str = arg.UTF8String;
            size_t len = strlen(str) + 1;
            if (pos + len >= sizeof(argv_buf) - 1) {
                current = saved_current;
                [ctx cleanup];
                NSLog(@"ISHShellExecutor: argv too long");
                failure = ISHShellExecutorErrorExecFailed;
                return ISHShellExecutorErrorExecFailed;
            }
            memcpy(argv_buf + pos, str, len);
            pos += len;
            exec_argc++;
        }
    }
    argv_buf[pos] = '\0'; // double-NUL terminate

    // Build envp as a C buffer (NUL-separated, double-NUL terminated)
    // NSString truncates at first NUL so we must use a raw C buffer
    char envp_buf[8192];
    size_t envp_pos = 0;

#define ENVP_APPEND(s) do { \
    const char *_s = (s); \
    size_t _len = strlen(_s) + 1; \
    if (envp_pos + _len < sizeof(envp_buf) - 256) { \
        memcpy(envp_buf + envp_pos, _s, _len); \
        envp_pos += _len; \
    } \
} while(0)

    ENVP_APPEND("TERM=xterm-256color");
    ENVP_APPEND("HOME=/root");
    ENVP_APPEND("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/bin");
    ENVP_APPEND("LANG=C.UTF-8");
    ENVP_APPEND("CHARSET=UTF-8");
    ENVP_APPEND("ENV=/etc/profile");
    ENVP_APPEND("OPENSSL_armcap=0");
    // Route browser-opening calls through the in-app WebKit preview.
    // Python's stdlib `webbrowser` module honours $BROWSER before probing
    // $DISPLAY, so setting it here lets `python -c "import webbrowser;
    // webbrowser.open(...)"` and similar non-login shell invocations reach
    // /usr/local/bin/minis-open which emits an OSC marker parsed by the host.
    ENVP_APPEND("BROWSER=/usr/local/bin/minis-open");

    // Inject device timezone so iSH userspace sees local time.
    // Use POSIX TZ format with a fixed name to avoid abbreviations like "GMT+8"
    // which contain +/- and confuse musl's TZ parser.
    {
        NSTimeZone *tz = [NSTimeZone systemTimeZone];
        NSInteger secs = tz.secondsFromGMT;
        NSInteger hrs  = secs / 3600;
        NSInteger mins = labs(secs % 3600) / 60;
        NSString *posixTZ;
        if (mins != 0) {
            posixTZ = [NSString stringWithFormat:@"LCL%+ld:%02ld",
                       (long)-hrs, (long)mins];
        } else {
            posixTZ = [NSString stringWithFormat:@"LCL%+ld",
                       (long)-hrs];
        }
        NSString *tzEnv = [NSString stringWithFormat:@"TZ=%@", posixTZ];
        ENVP_APPEND(tzEnv.UTF8String);
    }

    // Runtime compatibility env vars (matches xX_main_Xx.h / kernel/exec.c)
    ENVP_APPEND("GODEBUG=asyncpreemptoff=1");
    ENVP_APPEND("GOMAXPROCS=2");
    ENVP_APPEND("NO_COLOR=1");
    ENVP_APPEND("PYTHONMALLOC=malloc");
    ENVP_APPEND("PYTHONDONTWRITEBYTECODE=1");

    // Node-specific: LD_PRELOAD for zero_free.so
    if (is_node) {
        ENVP_APPEND("LD_PRELOAD=/lib/zero_free.so");
    }

    // Custom environment
    if (environment) {
        for (NSString *key in environment) {
            NSString *entry = [NSString stringWithFormat:@"%@=%@", key, environment[key]];
            ENVP_APPEND(entry.UTF8String);
        }
    }
    envp_buf[envp_pos] = '\0'; // double-NUL terminate

#undef ENVP_APPEND

    const char *envp = envp_buf;

    // Execute
    err = do_execve(exec_path, exec_argc, argv_buf, envp);
    if (err < 0) {
        current = saved_current;
        [ctx cleanup];
        NSLog(@"ISHShellExecutor: do_execve failed: %d", err);
        failure = ISHShellExecutorErrorExecFailed;
        return ISHShellExecutorErrorExecFailed;
    }

    // Registration and both committed reader retains precede task_start:
    // an immediate exit can no longer disappear before the registry exists.
    @synchronized(ctx) {
        if (ctx.didFinalize || ctx.isCompleted) return ISHShellExecutorErrorCancelled;
        ctx.guestPid = task->pid;
        ctx.result.pid = ctx.guestPid;
        @synchronized(_activeExecutions) {
            _activeExecutions[@(ctx.guestPid)] = ctx;
        }
    }
    [self startReaderForPipe:[ctx stdoutPipe][0] context:ctx isStdErr:NO];
    [self startReaderForPipe:[ctx stderrPipe][0] context:ctx isStdErr:YES];
    if (ctx.isCompleted) return ISHShellExecutorErrorCancelled;
    taskStartAttempted = YES;
    err = task_start(task);
    current = saved_current;
    if (err != 0) {
        NSLog(@"ISHShellExecutor: task_start failed: %d", err);
        return ISHShellExecutorErrorProcessCreationFailed;
    }
    launched = YES;
    BOOL terminationRequested;
    @synchronized(ticket) { terminationRequested = ticket.terminationRequested; }
    // Cancellation may have swept before the task became runnable. Repeat by
    // immutable token after successful start, never by a late root PID.
    if (terminationRequested) [self enqueueCleanupForToken:ctx.ticketToken];

    // Write stdinData to pipe in background, then close write end
    if (stdinData && stdinPipe[1] >= 0) {
        NSData *dataToWrite = [stdinData copy];
        int writeFd = stdinPipe[1];
        stdinPipe[1] = -1; // ownership transferred
        int guestPid = ctx.guestPid;
        NSLog(@"ISHShellExecutor[stdin]: scheduling stdinData write (%lu bytes) to fd=%d for pid=%d",
              (unsigned long)dataToWrite.length, writeFd, guestPid);
        dispatch_async(_readerQueue, ^{
            const uint8_t *bytes = dataToWrite.bytes;
            size_t remaining = dataToWrite.length;
            size_t totalWritten = 0;
            int flags = fcntl(writeFd, F_GETFL, 0);
            if (flags < 0 || fcntl(writeFd, F_SETFL, flags | O_NONBLOCK) < 0) {
                close(writeFd);
                return;
            }
            struct pollfd writer = { .fd = writeFd, .events = POLLOUT };
            while (remaining > 0 && !ctx.isCompleted) {
                int ready = poll(&writer, 1, 100);
                if (ready < 0) {
                    if (errno == EINTR) continue;
                    break;
                }
                if (ready == 0) continue;
                if (ctx.isCompleted || (writer.revents & (POLLERR | POLLHUP | POLLNVAL))) break;
                ssize_t written = write(writeFd, bytes, remaining);
                if (written < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
                if (written <= 0) break;
                bytes += written;
                remaining -= written;
                totalWritten += written;
            }
            close(writeFd);
            NSLog(@"ISHShellExecutor[stdin]: pipe write complete and closed — wrote %zu/%lu bytes for pid=%d",
                  totalWritten, (unsigned long)dataToWrite.length, guestPid);
        });
    } else if (!stdinData) {
        NSLog(@"ISHShellExecutor[stdin]: stdin is /dev/null for pid=%d", ctx.guestPid);
    }

    return ctx.guestPid;
    } @finally {
        BOOL cancelledDuringPrepare;
        @synchronized(ticket) { cancelledDuringPrepare = ticket.terminationRequested; }
        if (!launched && !taskStartAttempted && preparedTask != NULL && cancelledDuringPrepare) {
            taskStartAttempted = YES;
            int exitStart = ISHStartCancelledTask(preparedTask, ctx.ticketToken);
            if (exitStart != 0) {
                NSLog(@"ISHShellExecutor: exit-only task_start failed: %d; kernel recovery pending", exitStart);
            }
            // A first cleanup may already have finished before this tag existed.
            [self enqueueCleanupForToken:ctx.ticketToken];
        }
        current = saved_current;
        if (stdinPipe[0] >= 0) close(stdinPipe[0]);
        if (stdinPipe[1] >= 0) close(stdinPipe[1]);
        if (!launched) {
            if (attached) [self finalizeContext:ctx exitCode:-1 error:failure];
            else [ctx cleanup];
            // task_destroy's prepare-failure ABI is deliberately not guessed.
            // An unstarted guest task may still require kernel-side recovery.
        }
        if ([ctx releaseReader] <= 0 && ctx.isCompleted) [ctx closePipesIfNoReaders];
    }
}

+ (ISHShellExecutionResult *)executeCommandSync:(NSString *)command
                                        timeout:(NSTimeInterval)timeout
                                   lineCallback:(ISHShellLineCallback)lineCallback {

    // This legacy synchronous wrapper counts timeout only AFTER launch; a
    // kernel stalled during prepare is still outside its deadline boundary.
    ISHShellExecutionTicket *ticket = [self makeTicketWithFSContext:0];
    if (!ticket) {
        ISHShellExecutionResult *failure = [[ISHShellExecutionResult alloc] init];
        failure.error = ISHShellExecutorErrorProcessCreationFailed;
        failure.exitCode = -1;
        failure.output = failure.errorOutput = @"";
        return failure;
    }
    ticket.waitSemaphore = dispatch_semaphore_create(0);
    NSString *normalized = [command hasSuffix:@"\n"] ? command : [command stringByAppendingString:@"\n"];
    int pid = [self executeExecutable:@"/bin/sh" arguments:@[@"-c", normalized]
                         environment:nil stdinData:nil executionTicket:ticket
                        lineCallback:lineCallback completion:nil];
    if (pid >= 0) {
        dispatch_time_t waitTime = timeout > 0
            ? dispatch_time(DISPATCH_TIME_NOW, (int64_t)(timeout * NSEC_PER_SEC))
            : DISPATCH_TIME_FOREVER;
        if (dispatch_semaphore_wait(ticket.waitSemaphore, waitTime) != 0) {
            [self requestTerminationForTicket:ticket error:ISHShellExecutorErrorTimeout];
        }
    }
    ISHShellExecutionResult *result;
    @synchronized(ticket) { result = ticket.completedResult; }
    if (!result) {
        result = [[ISHShellExecutionResult alloc] init];
        result.error = pid < 0 ? (ISHShellExecutorError)pid : ISHShellExecutorErrorTimeout;
        result.exitCode = -1;
        result.pid = pid > 0 ? pid : 0;
        result.output = result.errorOutput = @"";
    }
    return result;
}

+ (BOOL)killProcess:(int)pid withSignal:(int)signal {
    struct siginfo_ info = SIGINFO_NIL;
    lock(&pids_lock);
    struct task *task = pid_get_task((dword_t)pid);
    if (task) {
        send_signal(task, signal, info);
    }
    unlock(&pids_lock);
    return task != NULL;
}

// Returns YES if `t` is `rootPid` or has `rootPid` somewhere in its parent
// chain. The caller must hold `pids_lock`. Bounded by MAX_PID to defend
// against a malformed parent cycle.
static BOOL ISHTaskIsDescendantOf(struct task *t, pid_t_ rootPid) {
    int hops = 0;
    while (t != NULL && hops < MAX_PID) {
        if (t->pid == rootPid) return YES;
        struct task *parent = t->parent;
        // [T-ish-killpg-stale-parent] Only walk to a parent the pid table
        // still vouches for.
        //
        // `task_destroy` zeroes the struct and defers the free, but it never
        // clears the `parent` pointer of the children it leaves behind. A
        // surviving child therefore keeps pointing at its dead parent, and
        // once `flush_deferred_frees` releases that block and malloc hands
        // the address to something else, the pointer is neither NULL nor
        // zeroed — it is another object's memory. Reading `->pid` off it
        // faulted (EXC_BAD_ACCESS at 0x7a8, 1.14(12), iOS 17.0).
        //
        // Re-deriving the parent through `pid_get_task` closes that: a
        // destroyed task's pid slot was set to NULL by `task_destroy`, and a
        // recycled slot returns a DIFFERENT pointer, which fails the identity
        // check below. Either way the walk stops instead of chasing freed
        // memory. Costs one table lookup per hop, on a path that already
        // scans MAX_PID entries.
        if (parent == NULL) return NO;
        pid_t_ parentPid = parent->pid;
        if (parentPid == 0 || parentPid >= MAX_PID) return NO;
        if (pid_get_task((dword_t)parentPid) != parent) return NO;
        t = parent;
        hops++;
    }
    return NO;
}

+ (void)killProcessGroup:(int)pid {
    // Signal every task belonging to this shell_execute command — matched
    // either by pgid OR by being a descendant of the root pid. The ancestry
    // arm is needed because busybox ash sometimes puts subshells into a
    // fresh process group (setpgid(0,0) for job control), which would leave
    // `sleep` and similar grandchildren invisible to a pgid-only sweep —
    // that is why the Stop button failed to kill `sleep`.
    //
    // Safety rails:
    //   - Refuse pid <= 1: pid 1 is init; an ancestry sweep rooted at init
    //     would match every task in iSH and tear the whole kernel down.
    //   - Re-verify the task pointer on the deferred SIGKILL pass. If the
    //     root has already exited and its pid was recycled to an unrelated
    //     task, the pointer comparison fails and we abort the sweep —
    //     otherwise we would kill an innocent command and its descendants.
    if (pid <= 1) {
        NSLog(@"ISHShellExecutor: refusing killProcessGroup for pid=%d", pid);
        return;
    }
    struct siginfo_ info = SIGINFO_NIL;

    lock(&pids_lock);
    struct task *rootTask = pid_get_task((dword_t)pid);
    pid_t_ pgid = 0;
    if (rootTask) {
        pgid = rootTask->group->pgid;
        for (int i = 2; i < MAX_PID; i++) {
            struct task *t = pid_get_task(i);
            if (!t) continue;
            // [T-ish-killpg-stale-parent] `task_destroy` zeroes the struct
            // before deferring the free, so a task still reachable from the
            // pid table can have a NULL group for that window.
            BOOL byPgid = (pgid != 0 && t->group != NULL && t->group->pgid == pgid);
            BOOL byAncestry = ISHTaskIsDescendantOf(t, (pid_t_)pid);
            if (byPgid || byAncestry) {
                send_signal(t, SIGTERM_, info);
            }
        }
    }
    unlock(&pids_lock);

    if (rootTask == NULL) {
        // Root already gone — don't schedule a delayed SIGKILL against a pid
        // that might already have been recycled.
        return;
    }

    // After a short delay, SIGKILL any survivors. SIGTERM may be caught
    // or, for tasks blocked inside host nanosleep, need the pthread_kill
    // wakeup that send_signal already issues — give the wakeup a chance
    // to land and the signal handler to run before escalating.
    int capturedPid = pid;
    struct task *capturedRoot = rootTask;
    pid_t_ capturedPgid = pgid;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(200 * NSEC_PER_MSEC)), dispatch_get_global_queue(0, 0), ^{
        lock(&pids_lock);
        // Verify the pid still maps to the same task struct we saw earlier.
        // pid_get_task returns NULL once task_destroy unlinks the task, and
        // a different pointer if the pid slot has been reused. Either way
        // the original command is already gone — skip the sweep.
        struct task *still = pid_get_task((dword_t)capturedPid);
        if (still != capturedRoot) {
            unlock(&pids_lock);
            return;
        }
        for (int i = 2; i < MAX_PID; i++) {
            struct task *t = pid_get_task(i);
            if (!t) continue;
            BOOL byPgid = (capturedPgid != 0 && t->group != NULL && t->group->pgid == capturedPgid);
            BOOL byAncestry = ISHTaskIsDescendantOf(t, (pid_t_)capturedPid);
            if (byPgid || byAncestry) {
                send_signal(t, SIGKILL_, info);
            }
        }
        unlock(&pids_lock);
    });
}

#pragma mark - Finalisation

/// Finish a run exactly once: capture output, stop readers, release the
/// registry entry, fire the completion callback and wake any sync waiter.
///
/// Both completion paths funnel through here — the normal exit notification
/// and the timeout self-heal — because they genuinely race. A command that
/// exits microseconds before its deadline delivers ISHProcessExitedNotification
/// while executeCommandSync is already returning from a timed-out semaphore
/// wait. Before this, the timeout path did none of this work (that was the
/// leak); doing it in both places without a guard would instead double-invoke
/// the completion callback and double-signal the semaphore.
///
/// `didFinalize` is the guard, swapped under @synchronized(ctx) so only one
/// caller can ever proceed. It is deliberately NOT `isCompleted`: that flag
/// means "readers should stop" and is set at other times too.
+ (void)finalizeContext:(ISHShellExecutionContext *)ctx
               exitCode:(int)exitCode
                  error:(ISHShellExecutorError)error {
    if (!ctx) return;

    // A cancellation claim precedes any racing prepare-failure finalization.
    // Read it before taking ctx so ticket -> ctx lock ordering is never nested.
    ISHShellExecutionTicket *owner = ctx.ticket;
    if (owner) {
        @synchronized(owner) {
            if (owner.terminationRequested) {
                error = owner.terminationError;
                exitCode = -1;
            }
        }
    }
    @synchronized(ctx) {
        if (ctx.didFinalize) return;
        ctx.didFinalize = YES;
        ctx.isCompleted = YES;
    }

    // Tell readers to stop. Any that are mid-poll return within 500ms (the
    // poll timeout) and the last one out closes the pipes.
    ctx.isCompleted = YES;

    ctx.result.exitCode = exitCode;
    ctx.result.error = error;
    if (ctx.result.duration == 0) {
        ctx.result.duration = -[ctx.startTime timeIntervalSinceNow];
    }

    // Same lock the readers append under — see the heap-corruption note in
    // processDidExit. Taking it here keeps -copy from tearing NSMutableString's
    // storage against a concurrent appendString.
    NSString *outCopy;
    NSString *errCopy;
    @synchronized(ctx.stdoutBuffer) { outCopy = [ctx.stdoutBuffer copy]; }
    @synchronized(ctx.stderrBuffer) { errCopy = [ctx.stderrBuffer copy]; }
    ctx.result.output = outCopy ?: @"";
    ctx.result.errorOutput = errCopy ?: @"";

    @synchronized(_activeExecutions) {
        NSNumber *key = @(ctx.guestPid);
        if (_activeExecutions[key] == ctx) [_activeExecutions removeObjectForKey:key];
    }

    // Closes now only if the readers have already gone; otherwise the last
    // reader out does it. Never closes an fd that is still being polled.
    [ctx closePipesIfNoReaders];

    ISHShellExecutionTicket *ticket = ctx.ticket;
    if (ticket) {
        @synchronized(ticket) {
            ticket.completedResult = ctx.result;
            if (ticket.context == ctx) ticket.context = nil;
        }
    }
    ISHShellCompletionCallback completion = ctx.completion;
    ctx.completion = nil;
    if (ctx.waitSemaphore) dispatch_semaphore_signal(ctx.waitSemaphore);
    if (completion) {
        ISHShellExecutionResult *result = ctx.result;
        dispatch_async(dispatch_get_main_queue(), ^{ completion(result); });
    }
}

+ (void)finalizeTimedOutPid:(int)pid {
    ISHShellExecutionContext *ctx;
    @synchronized(_activeExecutions) {
        ctx = _activeExecutions[@(pid)];
    }
    if (!ctx) {
        // Already finalised — the command exited normally just before the
        // deadline, or this is a second call. Nothing to do.
        return;
    }
    NSLog(@"ISHShellExecutor[timeout]: finalising pid=%d after kill (readers=%d)",
          pid, ctx.liveReaders);
    [self finalizeContext:ctx exitCode:-1 error:ISHShellExecutorErrorTimeout];
}

#pragma mark - Process Exit Handling

+ (void)processDidExit:(NSNotification *)notification {
    int pid = [notification.userInfo[@"pid"] intValue];
    int exitCode = [notification.userInfo[@"code"] intValue];
    NSNumber *tokenValue = notification.userInfo[@"executionToken"];
    uint64_t token = tokenValue.unsignedLongLongValue;
    // Never interpret a PID-only legacy notification as this execution.
    if (!tokenValue || !ISHExecutionIdentityIsValid(token)) return;

    ISHShellExecutionContext *ctx;
    @synchronized(_activeExecutions) {
        ctx = _activeExecutions[@(pid)];
        if (!ctx || ctx.guestPid != pid ||
            !ISHExecutionIdentityEqual(ctx.ticketToken, token)) return;
        // NOTE: intentionally NOT removed here. The removal now happens in
        // +finalizeContext:, after the drain window below. Removing it up
        // front left a ~200ms hole in which the sweeper could see no entry
        // for a run that had not finished, and in which a timeout could not
        // find the context it needed to finalise.
    }

    if (ctx.didFinalize) return;

    // Record duration immediately — the drain delay below must not be counted
    // as part of the command's runtime.
    ctx.result.duration = -[ctx.startTime timeIntervalSinceNow];
    NSUInteger outLen, errLen;
    @synchronized(ctx.stdoutBuffer) { outLen = ctx.stdoutBuffer.length; }
    @synchronized(ctx.stderrBuffer) { errLen = ctx.stderrBuffer.length; }
    NSLog(@"ISHShellExecutor[exit]: pid=%d exitCode=%d duration=%.2fs stdout=%lu stderr=%lu chars",
          pid, exitCode, ctx.result.duration,
          (unsigned long)outLen,
          (unsigned long)errLen);

    // DO NOT set isCompleted yet — let reader threads drain remaining pipe data.
    // Give readers 200ms to flush, then finalize.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 200 * NSEC_PER_MSEC),
                   dispatch_get_main_queue(), ^{
        [self finalizeContext:ctx
                     exitCode:exitCode
                        error:ISHShellExecutorErrorNone];
    });
}

#pragma mark - Pipe Reading

+ (void)startReaderForPipe:(int)fd context:(ISHShellExecutionContext *)ctx isStdErr:(BOOL)isStdErr {
    // Counted BEFORE the dispatch, not inside the block: between the two the
    // reader is already committed but has not started, and a cleanup landing
    // in that window would otherwise see zero readers and close the fd out
    // from under the one about to poll it.
    [ctx retainReader];
    OSAtomicIncrement32(&_globalLiveReaders);

    dispatch_async(_readerQueue, ^{
        @try {
            [self readPipe:fd context:ctx isStdErr:isStdErr];
        } @finally {
            // @finally so the count is right even if something above throws;
            // a reader that "leaked" only in the bookkeeping would keep the
            // pipes open forever, which is the bug being fixed here.
            int32_t remaining = OSAtomicDecrement32(&_globalLiveReaders);
            int32_t ctxRemaining = [ctx releaseReader];
            // Whoever leaves last closes the fds — by then nobody can poll
            // them. If the run was already finalised this is the close that
            // actually releases the descriptors. Using this reader's own
            // decrement result, so exactly one reader sees zero.
            if (ctxRemaining <= 0 && ctx.isCompleted) {
                [ctx closePipesIfNoReaders];
            }
            if (remaining < 0) {
                NSLog(@"ISHShellExecutor[reader]: BUG — global reader count went negative (%d)", remaining);
            }
        }
    });
}

/// Return the length of the longest prefix that is valid UTF-8.
/// If trailing bytes form an incomplete multi-byte sequence, they are excluded.
+ (NSUInteger)lastCompleteUTF8Length:(const uint8_t *)bytes length:(NSUInteger)len {
    if (len == 0) return 0;
    // Walk backwards from the end to find an incomplete UTF-8 sequence.
    // UTF-8 continuation bytes are 10xxxxxx (0x80-0xBF).
    NSUInteger i = len;
    // Skip trailing continuation bytes
    while (i > 0 && (bytes[i - 1] & 0xC0) == 0x80) {
        i--;
    }
    if (i == 0) {
        // All continuation bytes with no leading byte — not valid UTF-8
        return 0;
    }
    // bytes[i-1] is either ASCII (0x00-0x7F) or a leading byte (0xC0-0xFF)
    uint8_t lead = bytes[i - 1];
    if ((lead & 0x80) == 0) {
        // ASCII byte — sequence is complete
        return len;
    }
    // Determine expected sequence length from the leading byte
    NSUInteger seqLen;
    if ((lead & 0xE0) == 0xC0) seqLen = 2;
    else if ((lead & 0xF0) == 0xE0) seqLen = 3;
    else if ((lead & 0xF8) == 0xF0) seqLen = 4;
    else return i - 1; // Invalid leading byte — exclude it
    // Count actual bytes in this sequence: leading byte + continuation bytes after it
    NSUInteger actualLen = len - (i - 1);
    if (actualLen >= seqLen) {
        // Sequence is complete
        return len;
    }
    // Incomplete sequence — exclude it
    return i - 1;
}

+ (void)readPipe:(int)fd context:(ISHShellExecutionContext *)ctx isStdErr:(BOOL)isStdErr {
    char buffer[4096];
    NSMutableString *lineBuffer = [NSMutableString string];
    NSMutableString *outputBuffer = isStdErr ? ctx.stderrBuffer : ctx.stdoutBuffer;
    NSMutableData *pendingBytes = [NSMutableData data];
    const char *streamName = isStdErr ? "stderr" : "stdout";
    int idlePollCount = 0; // consecutive poll timeouts with no data

    // Use poll() instead of non-blocking read + usleep for reliable EOF detection
    struct pollfd pfd = { .fd = fd, .events = POLLIN };

    // Backstop against a reader outliving every other mechanism.
    //
    // The loop below exits on isCompleted, EOF, or a poll/read error, which
    // covers every path we know of. This deadline exists for the ones we do
    // not: a thread stuck here is not merely idle, it holds a slot in a
    // concurrent GCD queue, and enough of them stop the app from running any
    // command at all. Bounding the loop turns "device needs a restart" into
    // "one command's tail output was truncated".
    //
    // Deliberately generous — this must never cut a legitimately long-running
    // command short. A build or a package install can quietly produce nothing
    // for a long time; ISHShellExecutorReaderMaxLifetime is far beyond any
    // timeout a caller passes, so in practice only a stranded reader reaches
    // it. Hitting it at all is a bug, so it logs loudly.
    NSDate *readerDeadline = [NSDate dateWithTimeIntervalSinceNow:
                              ISHShellExecutorReaderMaxLifetime];

    while (!ctx.isCompleted) {
        if ([readerDeadline timeIntervalSinceNow] <= 0) {
            NSLog(@"ISHShellExecutor[reader]: %s hit the %.0fs lifetime cap for pid=%d "
                  @"(isCompleted=%d) — abandoning to protect the thread pool",
                  streamName, (double)ISHShellExecutorReaderMaxLifetime,
                  ctx.guestPid, ctx.isCompleted);
            break;
        }

        // poll with 500ms timeout so we periodically check isCompleted
        int pr = poll(&pfd, 1, 500);

        if (pr < 0) {
            if (errno == EINTR) continue;
            NSLog(@"ISHShellExecutor[reader]: %s poll error (errno=%d) for pid=%d", streamName, errno, ctx.guestPid);
            break; // poll error
        }

        if (pr == 0) {
            // Timeout — just loop back to check isCompleted
            idlePollCount++;
            // Log every 10s (20 x 500ms) of idle
            if (idlePollCount % 20 == 0) {
                NSLog(@"ISHShellExecutor[reader]: %s idle for %ds, pid=%d still running (no output, no exit)",
                      streamName, idlePollCount / 2, ctx.guestPid);
            }
            continue;
        }

        idlePollCount = 0; // reset on any activity

        // POLLHUP/POLLERR without POLLIN means pipe closed
        if ((pfd.revents & (POLLHUP | POLLERR)) && !(pfd.revents & POLLIN)) {
            NSLog(@"ISHShellExecutor[reader]: %s pipe closed (revents=0x%x) for pid=%d", streamName, pfd.revents, ctx.guestPid);
            break;
        }

        ssize_t bytesRead = read(fd, buffer, sizeof(buffer) - 1);

        if (bytesRead > 0) {
            [pendingBytes appendBytes:buffer length:bytesRead];

            NSUInteger safeLen = [self lastCompleteUTF8Length:(const uint8_t *)pendingBytes.bytes
                                                      length:pendingBytes.length];
            if (safeLen == 0 && pendingBytes.length > 0) {
                // No valid UTF-8 at all — fallback to Latin1 for the entire buffer
                NSString *chunk = [[NSString alloc] initWithBytes:pendingBytes.bytes
                                                           length:pendingBytes.length
                                                         encoding:NSISOLatin1StringEncoding];
                if (chunk) [lineBuffer appendString:chunk];
                [pendingBytes setLength:0];
            } else if (safeLen > 0) {
                NSString *chunk = [[NSString alloc] initWithBytes:pendingBytes.bytes
                                                           length:safeLen
                                                         encoding:NSUTF8StringEncoding];
                if (!chunk) {
                    // UTF-8 decode still failed — fallback to Latin1
                    chunk = [[NSString alloc] initWithBytes:pendingBytes.bytes
                                                     length:safeLen
                                                   encoding:NSISOLatin1StringEncoding];
                }
                if (chunk) [lineBuffer appendString:chunk];
                // Remove consumed bytes, keep incomplete trailing sequence
                [pendingBytes replaceBytesInRange:NSMakeRange(0, safeLen) withBytes:NULL length:0];
            }

            // Process complete lines
            [self processLines:lineBuffer
                       context:ctx
                  outputBuffer:outputBuffer
                      isStdErr:isStdErr];

        } else if (bytesRead == 0) {
            // EOF - pipe closed
            NSLog(@"ISHShellExecutor[reader]: %s EOF for pid=%d", streamName, ctx.guestPid);
            break;
        } else {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                continue; // spurious wakeup, poll again
            }
            NSLog(@"ISHShellExecutor[reader]: %s read error (errno=%d) for pid=%d", streamName, errno, ctx.guestPid);
            break;
        }
    }

    // Flush any remaining pending bytes (incomplete UTF-8 at EOF)
    if (pendingBytes.length > 0) {
        NSString *remaining = [[NSString alloc] initWithData:pendingBytes encoding:NSUTF8StringEncoding];
        if (!remaining) {
            remaining = [[NSString alloc] initWithData:pendingBytes encoding:NSISOLatin1StringEncoding];
        }
        if (remaining) [lineBuffer appendString:remaining];
        [pendingBytes setLength:0];
    }

    // Process any remaining partial line
    if (!ctx.isCompleted && lineBuffer.length > 0) {
        @synchronized(outputBuffer) {
            [outputBuffer appendString:lineBuffer];
        }

        if (ctx.lineCallback) {
            dispatch_async(dispatch_get_main_queue(), ^{
                if (ctx.didFinalize || ctx.isCompleted) return;
                ctx.lineCallback([lineBuffer copy], isStdErr);
            });
        }
    }
}

+ (void)processLines:(NSMutableString *)lineBuffer
             context:(ISHShellExecutionContext *)ctx
        outputBuffer:(NSMutableString *)outputBuffer
            isStdErr:(BOOL)isStdErr {

    while (!ctx.isCompleted) {
        NSRange newlineRange = [lineBuffer rangeOfString:@"\n"];
        if (newlineRange.location == NSNotFound) {
            break;
        }

        // Extract line without newline
        NSString *line = [lineBuffer substringToIndex:newlineRange.location];

        // Remove processed line from buffer
        [lineBuffer deleteCharactersInRange:NSMakeRange(0, newlineRange.location + 1)];

        // Add to output buffer
        @synchronized(outputBuffer) {
            [outputBuffer appendString:line];
            [outputBuffer appendString:@"\n"];
        }

        // Call line callback
        if (ctx.lineCallback) {
            dispatch_async(dispatch_get_main_queue(), ^{
                if (ctx.didFinalize || ctx.isCompleted) return;
                ctx.lineCallback(line, isStdErr);
            });
        }
    }
}

@end
