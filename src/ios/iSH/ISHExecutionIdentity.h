// Execution identity carried by the existing inherited tgroup.fs_context.
#ifndef ISH_EXECUTION_IDENTITY_H
#define ISH_EXECUTION_IDENTITY_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdatomic.h>

// The low word remains the filesystem router's session; generation zero is
// reserved for unowned/legacy tasks. A rejected input never consumes a slot.
static inline uint64_t ISHExecutionIdentityMake(uint64_t generation, uint64_t session) {
    if (generation == 0 || generation > UINT32_MAX || session > UINT32_MAX) return 0;
    return (generation << 32) | session;
}

static inline bool ISHExecutionIdentityIsValid(uint64_t token) {
    return (token >> 32) != 0;
}

static inline uint32_t ISHExecutionIdentitySession(uint64_t token) {
    return (uint32_t)token;
}

static inline bool ISHExecutionIdentityEqual(uint64_t expected, uint64_t actual) {
    return ISHExecutionIdentityIsValid(expected) && expected == actual;
}

// allocator stores the last issued generation, starts at zero, and saturates
// permanently at UINT32_MAX. CAS (not fetch_add) prevents rollover and reuse.
static inline bool ISHExecutionIdentityNext(_Atomic(uint64_t) *allocator,
                                           uint64_t session, uint64_t *token) {
    if (allocator == NULL || token == NULL || session > UINT32_MAX) return false;
    uint64_t previous = atomic_load_explicit(allocator, memory_order_relaxed);
    while (previous < UINT32_MAX) {
        if (atomic_compare_exchange_weak_explicit(allocator, &previous, previous + 1,
                                                  memory_order_relaxed, memory_order_relaxed)) {
            *token = ISHExecutionIdentityMake(previous + 1, session);
            return true;
        }
    }
    return false;
}

#endif
