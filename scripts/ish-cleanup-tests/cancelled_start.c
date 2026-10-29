// Exact production exit-only helper with a fake task_start (never real guest).
#include "ISHExecutionIdentity.h"
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#define SIGKILL_ 9
#define BOOL int
struct tgroup { uint64_t fs_context; bool doing_group_exit; unsigned group_exit_code; };
struct task { struct tgroup *group; };
static int calls,return_code,failures;
static int pids_lock;
static void lock(int *value){(void)value;}
static void unlock(int *value){(void)value;}
static bool saw_exit_only;
static int task_start(struct task *task){calls++;saw_exit_only=task->group->doing_group_exit&&task->group->group_exit_code==SIGKILL_;return return_code;}
/* PRODUCTION_CANCELLED_START */
#define CHECK(n,c) do{int ok=(c);printf("%s %s\n",ok?"PASS":"FAIL",n);if(!ok)failures++;}while(0)
int main(void){
 uint64_t token=ISHExecutionIdentityMake(12,4);struct tgroup group={.fs_context=token};struct task task={&group};
 CHECK("null task not started",ISHStartCancelledTask(NULL,token)==-1&&calls==0);
 struct task nogroup={NULL};CHECK("null group not started",ISHStartCancelledTask(&nogroup,token)==-1&&calls==0);
 CHECK("replacement identity rejected",ISHStartCancelledTask(&task,ISHExecutionIdentityMake(13,4))==-1&&calls==0&&!group.doing_group_exit);
 CHECK("owned cancelled task starts only exit path",ISHStartCancelledTask(&task,token)==0&&calls==1&&saw_exit_only);
 group.doing_group_exit=false;return_code=-11;
 CHECK("thread start failure propagated",ISHStartCancelledTask(&task,token)==-11&&calls==2&&saw_exit_only);
 printf("cancelled start failures=%d\n",failures);return failures?1:0;
}
