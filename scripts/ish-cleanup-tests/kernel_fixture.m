/* Host fake-kernel fixture. PRODUCTION_FUNCTIONS is replaced with exact bodies.
 * No real signals, guest task, device, network, or dispatch queue is used. */
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <unistd.h>
#include <errno.h>
typedef int BOOL;
typedef unsigned pid_t_;
typedef unsigned dword_t;
#define YES 1
#define NO 0
#define MAX_PID 32768
#define SIGTERM_ 15
#define SIGKILL_ 9
#define NSEC_PER_MSEC 1000000LL
#define DISPATCH_TIME_NOW 0
#define NSLog(...) ((void)0)
struct siginfo_ { int unused; };
#define SIGINFO_NIL ((struct siginfo_){0})
struct group { pid_t_ pgid; };
struct task { pid_t_ pid; struct task *parent; struct group *group; };
static struct task *table[MAX_PID];
static struct group groups[4];
static struct task init_task,root,child,unrelated,replacement;
static int terms[MAX_PID],kills[MAX_PID],lock_calls;
static pthread_mutex_t pids_lock=PTHREAD_MUTEX_INITIALIZER;
static void (^deferred)(void);
extern void *_Block_copy(const void *);
extern void _Block_release(const void *);
static void lock(pthread_mutex_t *p){__atomic_add_fetch(&lock_calls,1,__ATOMIC_RELAXED);pthread_mutex_lock(p);}
static void unlock(pthread_mutex_t *p){pthread_mutex_unlock(p);}
static struct task *pid_get_task(dword_t p){return p<MAX_PID?table[p]:NULL;}
static void send_signal(struct task *t,int sig,struct siginfo_ info){
 (void)info;if(sig==SIGTERM_)terms[t->pid]++;else if(sig==SIGKILL_)kills[t->pid]++;else abort();
}
static uint64_t dispatch_time(uint64_t n,int64_t d){return n+(uint64_t)d;}
static void *dispatch_get_global_queue(long a,long b){(void)a;(void)b;return NULL;}
static void dispatch_after(uint64_t t,void *q,void (^b)(void)){
 (void)t;(void)q;if(deferred)abort();deferred=_Block_copy(b);
}
/* PRODUCTION_FUNCTIONS */
static int failures;
static void check(const char *name,int ok,const char *kind){
 printf("{\"name\":\"%s\",\"kind\":\"%s\",\"observed\":%s}\n",name,kind,ok?"true":"false");
 if(!ok)failures++;
}
static void reset(void){
 if(deferred){_Block_release(deferred);deferred=NULL;}
 memset(table,0,sizeof(table));memset(terms,0,sizeof(terms));memset(kills,0,sizeof(kills));lock_calls=0;
 groups[0].pgid=1;groups[1].pgid=42;groups[2].pgid=100;groups[3].pgid=99;
 init_task=(struct task){1,NULL,&groups[0]};root=(struct task){42,&init_task,&groups[1]};
 child=(struct task){43,&root,&groups[1]};unrelated=(struct task){100,&init_task,&groups[2]};
 replacement=(struct task){42,&init_task,&groups[3]};
 table[1]=&init_task;table[42]=&root;table[43]=&child;table[100]=&unrelated;
}
static void run_deferred(void){if(!deferred)abort();void (^b)(void)=deferred;deferred=NULL;b();_Block_release(b);}
static atomic_int entered,finished;
static void *blocked_worker(void *unused){(void)unused;atomic_store(&entered,1);productionKillProcessGroup(42);atomic_store(&finished,1);return NULL;}
static int join_bounded(pthread_t t){
 struct timespec ts;clock_gettime(CLOCK_REALTIME,&ts);ts.tv_sec+=2;return pthread_timedjoin_np(t,NULL,&ts)==0;
}
int main(void){
 alarm(8);
 reset();productionKillProcessGroup(0);productionKillProcessGroup(1);
 check("refuse_init_and_invalid_pid",lock_calls==0&&!deferred&&!terms[43],"safety");
 reset();productionKillProcessGroup(777);
 check("missing_root_no_escalation",!deferred&&lock_calls==1,"safety");
 reset();productionKillProcessGroup(42);
 check("term_only_target_tree",terms[42]==1&&terms[43]==1&&!terms[100],"safety");
 run_deferred();check("normal_sigkill_escalates",kills[42]==1&&kills[43]==1&&!kills[100],"safety");
 reset();child.group=&groups[3];productionKillProcessGroup(42);run_deferred();
 check("descendant_new_pgid_still_signalled",terms[43]==1&&kills[43]==1,"safety");
 reset();productionKillProcessGroup(42);table[42]=&replacement;run_deferred();
 check("deferred_pid_reuse_not_killed",kills[42]==0&&kills[43]==0,"safety");
 reset();productionKillProcessGroup(42);table[42]=NULL;run_deferred();
 check("root_exit_survivor_skips_sigkill",terms[43]==1&&kills[43]==0,"baseline_defect");
 reset();table[42]=&replacement;productionKillProcessGroup(42);
 check("stale_initial_pid_request_signals_replacement",terms[42]==1,"baseline_defect");
 check("stale_parent_identity_not_chased",terms[43]==0&&terms[100]==0,"safety");
 reset();atomic_store(&entered,0);atomic_store(&finished,0);pthread_mutex_lock(&pids_lock);
 pthread_t t;if(pthread_create(&t,NULL,blocked_worker,NULL))return 3;
 for(int i=0;i<100&&__atomic_load_n(&lock_calls,__ATOMIC_RELAXED)==0;i++){struct timespec ts={0,1000000};nanosleep(&ts,NULL);}
 struct timespec ts={0,30000000};nanosleep(&ts,NULL);
 int was_blocked=atomic_load(&entered)&&__atomic_load_n(&lock_calls,__ATOMIC_RELAXED)>0&&!atomic_load(&finished);
 pthread_mutex_unlock(&pids_lock);
 if(!join_bounded(t)){fprintf(stderr,"worker failed to join\n");return 3;}
 check("production_body_blocks_on_fake_pids_lock",was_blocked&&atomic_load(&finished),"fault_injection");
 reset();printf("{\"fixture_failures\":%d}\n",failures);return failures?1:0;
}
