#!/usr/bin/env python3
"""Run real source-derived ObjC argument parsing; only RPC/output sinks are fixtures."""
from pathlib import Path
import argparse,hashlib,json,shlex,subprocess,sys,tempfile

ROOT=Path(__file__).resolve().parents[1]
BASELINE='922927a2b2fbfb19e7b26155238a67eb0e28af83'
ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--output',type=Path)
args=ap.parse_args()
OUT=args.output or Path(tempfile.mkdtemp(prefix='debug-cli-operands-'))
OUT.mkdir(parents=True,exist_ok=True)
def original(path):
    return subprocess.check_output(['git','show',BASELINE+':'+path],cwd=ROOT,text=True)
utils=(ROOT/'src/ios/NativeOffloads/NativeOffloadUtils.m').read_text()
debug=(ROOT/'src/ios/NativeOffloads/DebugOffload.m').read_text()
old_debug=original('src/ios/NativeOffloads/DebugOffload.m')
def region(text,start,end):
    assert text.count(start)==1
    at=text.index(start); return text[at:text.index(end,at)].strip()
parser=region(utils,'NSArray<NSString *> *noff_positional_args(', '// ── Date parsing')
selector=region(old_debug,'static NSString *_Nullable second_positional(', '/// Build NSNumber')
candidate=region(debug,'static NSString *_Nullable second_positional(', '/// Build NSNumber')
inspect=region(debug,'static int cmd_inspect(', 'static int cmd_highlight(')
assert parser==region(original('src/ios/NativeOffloads/NativeOffloadUtils.m'),'NSArray<NSString *> *noff_positional_args(', '// ── Date parsing')
assert inspect==region(old_debug,'static int cmd_inspect(', 'static int cmd_highlight(')
# On Apple the original functions compile verbatim. GNU's legacy runtime
# needs equivalent message spelling for one array subscript (index unchanged).
adapt=lambda s:s.replace('pos[1]','[pos objectAtIndex:1]') if sys.platform!='darwin' else s
baseline=adapt(selector)
fixed=adapt(candidate)
prefix=r'''
#import <Foundation/Foundation.h>
#include <stdio.h>
#include <string.h>
static NSString *const TOOL_NAME=@"minis-debug";
static NSString *const NOFF_ERR_INVALID_ARGS=@"invalid_args";
enum { NOFF_EXIT_INVALID_ARGS=2 };
static NSDictionary *lastParams=nil;
static NSString *lastMethod=nil;
static NSDictionary *lastError=nil;
static NSDictionary *noff_json_error(NSString *t,NSString *a,NSString *c,NSString *m) {
    return @{ @"ok":@NO, @"error":@{ @"code":c,@"message":m } };
}
static void noff_emit_json(int fd,NSDictionary *value,BOOL c,BOOL q) { lastError=value; }
static int emit_rpc(int o,int e,NSString *a,NSString *m,NSDictionary *p,BOOL c,BOOL q) {
    lastMethod=m;lastParams=p;return 0;
}
'''
main=r'''
int main(void) { @autoreleasepool {
    const char *vectors[][6]={
        {"minis-debug","inspect","0x123","--compact",NULL,NULL},
        {"minis-debug","inspect","0x123",NULL,NULL,NULL},
        {"minis-debug","inspect","--compact",NULL,NULL,NULL},
        {"minis-debug","inspect","0x123","0x123","--compact",NULL},
        {"minis-debug","inspect","0x123","0x456","--compact",NULL}
    };
    NSArray *names=@[@"one_address_compact",@"one_address",@"missing_address",@"duplicate_address_compat",@"different_second_address"];
    NSMutableArray *results=[NSMutableArray array];
    for (int row=0;row<5;row++) {
        char *argv[6];int argc=0;
        while(vectors[row][argc]) {argv[argc]=(char *)vectors[row][argc];argc++;}
        lastParams=nil;lastMethod=nil;lastError=nil;
        int rc=cmd_inspect(argc,argv,-1,-1,YES,NO);
        [results addObject:@{ @"case":[names objectAtIndex:row],@"exit":@(rc),
          @"method":lastMethod?:[NSNull null],@"address":[lastParams objectForKey:@"address"]?:[NSNull null],
          @"error":lastError?:[NSNull null] }];
    }
    NSData *d=[NSJSONSerialization dataWithJSONObject:results options:0 error:NULL];
    puts([[[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] UTF8String]);
} return 0; }
'''
if sys.platform=='darwin':
    compiler=['xcrun','clang','-fobjc-arc'];flags=[];libs=['-framework','Foundation']
else:
    include=subprocess.check_output(['gcc','-print-file-name=include'],text=True).strip()
    compiler=['clang','-I'+include]
    flags=shlex.split(subprocess.check_output(['gnustep-config','--objc-flags'],text=True))
    libs=shlex.split(subprocess.check_output(['gnustep-config','--base-libs'],text=True))
reports={}
for name,sel in [('baseline',baseline),('candidate',fixed)]:
    text=prefix+'\n'+parser+'\n'+sel+'\n'+inspect+'\n'+main
    # GNU objc's YES/NO macros require the parenthesized NSNumber syntax.
    text=text.replace('@NO','@(NO)')
    source=OUT/(name+'.m');source.write_text(text)
    executable=OUT/name
    cmd=[*compiler,*flags,'-Werror','-Wno-unused-function',str(source),'-o',str(executable),*libs]
    build=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (OUT/(name+'-compile.log')).write_text(build.stdout)
    if build.returncode:print(build.stdout);raise SystemExit('compile failure is INVALID, not a red test')
    reports[name]=json.loads(subprocess.check_output([str(executable)],text=True))
    (OUT/(name+'.json')).write_text(json.dumps(reports[name],indent=2)+'\n')
a=reports['baseline'];b=reports['candidate']
assert a[0]['exit']==2 and a[0]['method'] is None and a[0]['address'] is None
assert b[0]['exit']==0 and b[0]['method']=='debug.inspect' and b[0]['address']=='0x123'
assert a[1]['exit']==2 and b[1]['exit']==0
assert a[2]['exit']==2 and b[2]['exit']==2
assert a[3]['exit']==0 and b[3]['exit']==0 and a[3]['address']==b[3]['address']=='0x123'
assert a[4]['address']=='0x456' and b[4]['address']=='0x123'
summary={'native_foundation_argument_failure_reproduced':True,'candidate_from_production_source':True,
         'baseline_canonical_inspect_exit':a[0]['exit'],'fixed_canonical_inspect_exit':b[0]['exit'],
         'duplicate_same_address_reads_same_target_on_both':True,'baseline_commit':BASELINE,
         'candidate_head_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         'source_parser_sha256':hashlib.sha256(parser.encode()).hexdigest(),'baseline_selector_sha256':hashlib.sha256(selector.encode()).hexdigest(),
         'candidate_selector_sha256':hashlib.sha256(candidate.encode()).hexdigest(),'platform':sys.platform,
         'limits':'Actual parser/selector/cmd_inspect with RPC and output fixtures. Apple source verbatim; GNU array-subscript spelling adapted without changing its index. Not UIKit or full iOS shell verification.'}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
