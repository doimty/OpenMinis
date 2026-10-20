// Host tests for the exact production token-signal helper injected by runner.
#include "ISHExecutionIdentity.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#define MAX_PID 32768
#define BOOL int
#define YES 1
#define NO 0
#define SIGINFO_NIL ((struct siginfo_){0})
typedef unsigned dword_t;
struct siginfo_ { int unused; };
struct tgroup { uint64_t fs_context; };
struct task { struct tgroup *group; };
static pthread_mutex_t pids_lock=PTHREAD_MUTEX_INITIALIZER;
static struct task *table[MAX_PID];
static struct tgroup owned,other;
static struct task root,child,replacement,init_task,last;
static unsigned terms[MAX_PID],kills[MAX_PID];
static int failures;
static int outOfRangeReads;
static int trylock(pthread_mutex_t *p){return pthread_mutex_trylock(p);}
static void unlock(pthread_mutex_t *p){pthread_mutex_unlock(p);}
static struct task *pid_get_task(dword_t pid){if(pid>=MAX_PID){outOfRangeReads++;return NULL;}return table[pid];}
static void send_signal(struct task *task,int sig,struct siginfo_ info){
 (void)info;for(unsigned i=1;i<=MAX_PID;i++)if(table[i]==task){if(sig==15)terms[i]++;else if(sig==9)kills[i]++;return;}abort();
}
/* PRODUCTION_TOKEN_SIGNAL */
#define CHECK(n,c) do{int ok=(c);printf("%s %s\n",ok?"PASS":"FAIL",n);if(!ok)failures++;}while(0)
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
int main(void){
 uint64_t token=ISHExecutionIdentityMake(12,4);
 owned.fs_context=token;other.fs_context=ISHExecutionIdentityMake(13,4);
 root.group=child.group=init_task.group=last.group=&owned;replacement.group=&other;
 table[1]=&init_task;table[42]=&root;table[43]=&child;
 table[MAX_PID-1]=&last;
 CHECK("unowned token rejected",ISHSignalTasksForExecutionToken(4,15)&&!terms[42]);
 CHECK("own tree TERM scan",ISHSignalTasksForExecutionToken(token,15)&&terms[42]==1&&terms[43]==1);
 CHECK("init never signalled",terms[1]==0);
 CHECK("highest valid PID below MAX_PID scanned",terms[MAX_PID-1]==1);
 CHECK("never probe exclusive MAX_PID",outOfRangeReads==0);
 table[42]=NULL;
 CHECK("root gone child still KILL",ISHSignalTasksForExecutionToken(token,9)&&kills[43]==1);
 table[42]=&replacement;
 CHECK("reused PID same route new generation untouched",ISHSignalTasksForExecutionToken(token,9)&&kills[42]==0&&kills[43]==2);
 replacement.group=NULL;
 CHECK("null group tolerated",ISHSignalTasksForExecutionToken(token,15));
 pthread_mutex_lock(&pids_lock);double start=now();int busy=!ISHSignalTasksForExecutionToken(token,9);double elapsed=now()-start;pthread_mutex_unlock(&pids_lock);
 CHECK("busy kernel lock returns without blocking",busy&&elapsed<0.25);
 CHECK("lock remains usable after busy pass",ISHSignalTasksForExecutionToken(token,9));
 printf("token signal failures=%d\n",failures);return failures?1:0;
}
