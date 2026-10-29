#!/usr/bin/env python3
"""Bounded production-slice characterization, NOT an iOS/Swift runtime test.
--gate returns 1 for observed baseline defects; --characterize validates that
our fault injection really exposes them. No device, network or real kill call.
"""
import argparse,hashlib,json,pathlib,re,shutil,subprocess,sys,tempfile
from tree_sitter import Language,Parser
import tree_sitter_swift
ROOT=pathlib.Path(__file__).resolve().parents[1]
OBJC='src/ios/iSH/ISHShellExecutor.m';SWIFT='src/ios/Agent/ISH/ISHExecutionCoordinator.swift'
def sha(b):return hashlib.sha256(b).hexdigest()
def masked(text):
 rx=re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',re.S)
 return rx.sub(lambda m:''.join('\n' if c=='\n' else ' ' for c in m[0]),text)
def c_unit(text,signature):
 if text.count(signature)!=1:raise ValueError('non-unique production signature')
 start=text.index(signature);clean=masked(text);o=clean.index('{',start);depth=1;p=o+1
 while depth and p<len(clean):
  depth+=(clean[p]=='{')-(clean[p]=='}');p+=1
 if depth:raise ValueError('unbalanced production body')
 return text[start:p],dict(start_line=text.count('\n',0,start)+1,end_line=text.count('\n',0,p)+1,sha256=sha(text[start:p].encode()),body_sha256=sha(text[o:p].encode()))
def walk(n):
 yield n
 for c in n.children:yield from walk(c)
def swift_units(raw):
 t=Parser(Language(tree_sitter_swift.language())).parse(raw)
 def text(n):return raw[n.start_byte:n.end_byte].decode()
 units={}
 for name,kind,prefix in [('timeout','property_declaration','let work = DispatchWorkItem'),('schedule','call_expression','Self.killQueue.asyncAfter(')]:
  found=[n for n in walk(t.root_node) if n.type==kind and text(n).startswith(prefix)]
  if len(found)!=1 or found[0].has_error:raise ValueError('unrecognised Swift production layout: '+name)
  n=found[0];units[name]=(raw[n.start_byte:n.end_byte],n)
 actions=[];mapping={'claimResume':'claim','ISHShellExecutor.killProcessGroup':'kill','ISHShellExecutor.finalizeTimedOutPid':'finalize','pidCallback':'clear_pid','continuation.resume':'resume'}
 for n in walk(units['timeout'][1]):
  if n.type=='call_expression':
   head=re.sub(r'\s+','',text(n.children[0]))
   if head in mapping:actions.append(mapping[head])
 if actions!=['claim','kill','finalize','clear_pid','resume']:raise ValueError('unsupported action topology; update characterization rather than declaring it passed')
 stops=[n for n in walk(t.root_node) if n.type=='function_declaration' and n.child_by_field_name('name') is not None and text(n.child_by_field_name('name')) in ('stopCurrentCommand','stopAllNonisolated')]
 if len(stops)!=3:raise ValueError('expected three production Stop paths')
 for i,n in enumerate(stops):
  calls=[c for c in walk(n) if c.type=='call_expression']
  queues=[c for c in calls if re.sub(r'\s+','',text(c.children[0]))=='Self.killQueue.async']
  kills=[c for c in calls if re.sub(r'\s+','',text(c.children[0]))=='ISHShellExecutor.killProcessGroup']
  if not queues or not kills or any(not any(q.start_byte<k.start_byte and k.end_byte<q.end_byte for q in queues) for k in kills):raise ValueError('Stop kill is not inside its async queue closure')
  units['stop_'+str(i)]=(raw[n.start_byte:n.end_byte],n)
 queues=[n for n in walk(t.root_node) if n.type=='property_declaration' and text(n).startswith('static let killQueue = DispatchQueue(')]
 if len(queues)!=1 or 'attributes:' in re.sub(r'\s+','',text(queues[0])):raise ValueError('killQueue is not the known serial queue')
 n=queues[0];units['queue']=(raw[n.start_byte:n.end_byte],n)
 return units,actions

def schedule_replay(actions):
 # Tiny deterministic dependency interpreter; NOT Swift/GCD execution. The
 # topology and action order come from verified AST units, not copied policy.
 blocked_stop=['kill'];pending_deadline=list(actions);claimed=False;trace=[]
 if blocked_stop[0]=='kill':trace.append('queue head blocks on kill; deadline remains queued')
 first={'name':'deadline_waits_behind_blocked_stop_on_same_serial_queue','observed':bool(pending_deadline),'trace':trace[:]}
 executed=[]
 for action in actions:
  if action=='claim':claimed=True
  if action=='kill':break
  executed.append(action)
 normal_exit_can_resume=not claimed
 return [first,{'name':'timeout_claims_completion_before_blocking_kill','observed':claimed and not normal_exit_can_resume and 'resume' not in executed,'executed':executed},
 {'name':'finalize_and_pid_clear_not_reached_when_kill_blocks','observed':all(x not in executed for x in ['finalize','clear_pid']),'executed':executed}]

def run_fixture(code,out,name):
 source=out/(name+'.m');binary=out/name;source.write_text(code)
 compiler=shutil.which('clang')
 if not compiler:raise RuntimeError('clang required for real production C-body host execution')
 cmd=[compiler,'-D_GNU_SOURCE','-std=gnu11','-fblocks','-Wall','-Wextra','-Werror','-O1',str(source),'-pthread','-L/usr/local/lib','-lBlocksRuntime','-o',str(binary)]
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=35)
 (out/(name+'-compile.log')).write_text(r.stdout+r.stderr)
 if r.returncode:raise RuntimeError('host compile failed; see '+str(out/(name+'-compile.log')))
 r=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
 (out/(name+'-run.log')).write_text(r.stdout+r.stderr)
 return r.returncode,[json.loads(s) for s in r.stdout.splitlines() if s.startswith('{')]

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',required=True,type=pathlib.Path);g=ap.add_mutually_exclusive_group(required=True);g.add_argument('--characterize',action='store_true');g.add_argument('--gate',action='store_true');args=ap.parse_args()
 out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
 src=(ROOT/OBJC).read_text();helper,hm=c_unit(src,'static BOOL ISHTaskIsDescendantOf(');kill,km=c_unit(src,'+ (void)killProcessGroup:(int)pid {')
 ckill=kill.replace('+ (void)killProcessGroup:(int)pid {','static void productionKillProcessGroup(int pid) {',1)
 assert ckill[ckill.index('{'):]==kill[kill.index('{'):]
 template=(ROOT/'scripts/ish-cleanup-tests/kernel_fixture.m').read_text();production=helper+'\n'+ckill
 generated=template.replace('/* PRODUCTION_FUNCTIONS */',production)
 rc,cases=run_fixture(generated,out,'baseline-kernel')
 if rc or not cases or cases[-1].get('fixture_failures')!=0:raise RuntimeError('baseline fixture expectations did not hold')
 units,actions=swift_units((ROOT/SWIFT).read_bytes())
 manifest={'objc_source_sha256':sha(src.encode()),'helper':hm,'kill':km,'swift_source_sha256':sha((ROOT/SWIFT).read_bytes()),'swift_units':{}}
 for name,(b,node) in units.items():
  (out/(name+'.swift')).write_bytes(b);manifest['swift_units'][name]={'start_byte':node.start_byte,'end_byte':node.end_byte,'sha256':sha(b)}
 (out/'production-helper.c').write_text(helper);(out/'production-kill.m').write_text(kill)
 # Replay only the exact source-derived order above, with a blocked kill API.
 # This is dependency/schedule evidence, explicitly not native Swift execution.
 model=schedule_replay(actions)
 # Mutate the extracted production body, not a hand-written replacement.
 mutations={
 'remove_deferred_identity_guard':production.replace('if (still != capturedRoot) {','if (still == capturedRoot && 0) {'),
 'remove_ancestry_matching':re.sub(r'BOOL byAncestry = ISHTaskIsDescendantOf\([^;]+;', 'BOOL byAncestry = NO;',production),
 'allow_init_as_root':production.replace('if (pid <= 1) {','if (pid < 1) {'),
 'remove_parent_identity_guard':production.replace('if (pid_get_task((dword_t)parentPid) != parent) return NO;', 'if (0) return NO;')}
 caught=[]
 for name,mutant in mutations.items():
  if mutant==production:raise ValueError('mutation did not change production code: '+name)
  # Removing ancestry makes its now-unused helper trigger -Werror. Keep a
  # reference in main so failure must come from behaviour, not compilation.
  code=template.replace('/* PRODUCTION_FUNCTIONS */',mutant).replace(' alarm(8);',' (void)&ISHTaskIsDescendantOf; alarm(8);')
  result,rows=run_fixture(code,out,name)
  failed=[x['name'] for x in rows if x.get('observed') is False]
  if result!=1 or not failed:raise RuntimeError('mutation not caught by runtime assertions: '+name)
  caught.append({'name':name,'caught_by':failed,'native_exit':result})
 conditional=[x['name'] for x in model if x['observed']]
 defects=[x['name'] for x in cases if x.get('kind')=='baseline_defect' and x['observed']]
 gate_exit=1 if defects or conditional else 0
 expected_exit=gate_exit if args.gate else 0
 result={'scope':'native extracted production C bodies with fake kernel/dispatch; AST-driven conditional Swift dependency analysis, not native Swift/iSH/iOS', 'mode':'gate' if args.gate else 'characterize','harness_ok':True,'expected_exit_code':expected_exit,'baseline_gate_exit_code':gate_exit,'root':str(ROOT),'source_manifest':manifest,'native_cases':cases[:-1],'swift_conditional_dependency_analysis':model,'swift_assumptions':['kill API remains blocked','serial queue dependency semantics','normal completion obeys claimResume once guard'],'runtime_mutants_caught':caught,'observed_native_baseline_hazards':defects,'conditional_dependency_findings':conditional,'ready_to_release':False,'production_source_modified':False}
 (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps({'native_observations':len(cases)-1,'native_hazards':len(defects),'conditional_dependency_findings':len(conditional),'native_mutants_caught':len(caught),'ready_to_release':False,'mode':result['mode'],'expected_exit_code':expected_exit},ensure_ascii=False))
 return expected_exit
if __name__=='__main__':
 try:sys.exit(main())
 except Exception as e:print('ERROR:',str(e),file=sys.stderr);sys.exit(2)
