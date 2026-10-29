// Exercises the production identity header, not a reimplementation.
#include "ISHExecutionIdentity.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define NTHREAD 8
#define EACH 512
static _Atomic(uint64_t) allocator=0;
static uint64_t tokens[NTHREAD*EACH];
static int failures;
#define CHECK(n,c) do { int ok=(c); printf("%s %s\n",ok?"PASS":"FAIL",n);if(!ok)failures++; } while(0)
static void *allocate(void *arg){
 size_t idx=(size_t)arg;
 for(size_t i=0;i<EACH;i++)if(!ISHExecutionIdentityNext(&allocator,(uint64_t)idx+1,&tokens[idx*EACH+i]))abort();
 return NULL;
}
static int compare(const void *a,const void *b){uint64_t x=*(const uint64_t*)a,y=*(const uint64_t*)b;return (x>y)-(x<y);}
int main(void){
 CHECK("zero generation invalid",ISHExecutionIdentityMake(0,1)==0&&!ISHExecutionIdentityIsValid(0));
 CHECK("session overflow refused",ISHExecutionIdentityMake(1,(uint64_t)UINT32_MAX+1)==0);
 CHECK("generation overflow refused",ISHExecutionIdentityMake((uint64_t)UINT32_MAX+1,1)==0);
 CHECK("global route retains identity",ISHExecutionIdentityIsValid(ISHExecutionIdentityMake(1,0))&&ISHExecutionIdentitySession(ISHExecutionIdentityMake(1,0))==0);
 uint64_t a=ISHExecutionIdentityMake(1,17),b=ISHExecutionIdentityMake(2,17);
 CHECK("different executions never equal",!ISHExecutionIdentityEqual(a,b));
 CHECK("same session low bits preserved",ISHExecutionIdentitySession(a)==17&&ISHExecutionIdentitySession(b)==17);
 CHECK("unowned legacy task never matches",!ISHExecutionIdentityEqual(0,0)&&!ISHExecutionIdentityEqual(17,17));
 CHECK("exact ticket matches",ISHExecutionIdentityEqual(a,a));
 pthread_t threads[NTHREAD];
 for(size_t i=0;i<NTHREAD;i++)if(pthread_create(&threads[i],NULL,allocate,(void*)i))return 2;
 for(size_t i=0;i<NTHREAD;i++)if(pthread_join(threads[i],NULL))return 2;
 qsort(tokens,NTHREAD*EACH,sizeof(uint64_t),compare);int unique=1;
 for(size_t i=1;i<NTHREAD*EACH;i++)if(tokens[i]==tokens[i-1])unique=0;
 CHECK("4096 concurrent allocations unique",unique&&atomic_load(&allocator)==NTHREAD*EACH);
 _Atomic(uint64_t) saturated=UINT32_MAX-1;uint64_t last=0,next=UINT64_C(0xdeadbeef);
 CHECK("last generation allocates",ISHExecutionIdentityNext(&saturated,99,&last)&&last==ISHExecutionIdentityMake(UINT32_MAX,99));
 CHECK("saturation never wraps",!ISHExecutionIdentityNext(&saturated,99,&next)&&next==UINT64_C(0xdeadbeef)&&atomic_load(&saturated)==UINT32_MAX);
 uint64_t previous=atomic_load(&allocator);
 CHECK("bad input does not consume identity",!ISHExecutionIdentityNext(&allocator,(uint64_t)UINT32_MAX+1,&next)&&atomic_load(&allocator)==previous);
 CHECK("null outputs rejected",!ISHExecutionIdentityNext(&allocator,1,NULL)&&!ISHExecutionIdentityNext(NULL,1,&next));
 printf("identity failures=%d\n",failures);return failures?1:0;
}
