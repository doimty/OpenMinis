#include "HostFileAccessProbe.h"
#include <stdio.h>

int main(void) {
    OMHostFileProbeResult r = OMHostFileProbeReadSample();
    printf("%d\t%d\t%s\t%s\t%llu\t%llu\t%llu\n",
           r.outcome, r.error_number, r.stage ? r.stage : "", r.sample_id ? r.sample_id : "",
           (unsigned long long)r.device, (unsigned long long)r.inode, (unsigned long long)r.size);
    return 0;
}
