#include "RootManagementClient.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>

int main(void) {
    assert(OMRootManagementClientVersion() == 1);
    char output[4096];
    OMRootManagementResult result = OMRootManagementRun((OMRootOperation)999, output, sizeof(output));
    assert(result.error_number == EINVAL && result.exit_code == -1 && result.output_bytes == 0);
    result = OMRootManagementRun(OMRootOperationIdentity, NULL, 0);
    assert(result.error_number == EINVAL && result.exit_code == -1);
    /* The standalone harness runs on the main thread: valid operations must
     * fail before resolving any helper path, persona or process-spawn API. */
    result = OMRootManagementRun(OMRootOperationIdentity, output, sizeof(output));
    assert(result.error_number == EDEADLK && result.exit_code == -1 && output[0] == '\0');
    puts("PASS root client: linked version, invalid enum/buffer refusal, main-thread refusal without spawn");
    return 0;
}
