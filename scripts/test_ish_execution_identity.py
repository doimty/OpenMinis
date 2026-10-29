#!/usr/bin/env python3
"""Execute the production identity header and extracted token-signal helper.
Fake kernel only; NOT full ObjC, Swift, iSH or iOS execution validation.
"""
import argparse,hashlib,importlib.util,json,pathlib,shutil,subprocess,sys
P=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('baseline_cleanup',P/'scripts/test_ish_cleanup_boundaries.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',required=True,type=pathlib.Path);a=ap.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
 compiler=shutil.which('cc') or shutil.which('clang')
 if not compiler:raise RuntimeError('a C compiler is required')
 src=(P/'src/ios/iSH/ISHShellExecutor.m').read_text(encoding="utf-8")
 helper,manifest=b.c_unit(src,'static BOOL ISHSignalTasksForExecutionToken(')
 template=(P/'scripts/ish-cleanup-tests/token_signal.c').read_text(encoding="utf-8")
 code=template.replace('/* PRODUCTION_TOKEN_SIGNAL */',helper)
 header=P/'src/ios/iSH/ISHExecutionIdentity.h'
 results=[]
 def run(name,code=None,path=None,mutant=False):
  if code is not None:path=out/(name+'.c');path.write_text(code)
  exe=out/name
  r=subprocess.run([compiler,'-D_POSIX_C_SOURCE=200809L','-std=c11','-Wall','-Wextra','-Werror','-O1','-pthread','-I',str(header.parent),str(path),'-o',str(exe)],capture_output=True,text=True,timeout=35)
  (out/(name+'-compile.log')).write_text(r.stdout+r.stderr)
  if r.returncode:raise RuntimeError(name+' failed to compile (not a caught mutant)')
  r=subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
  (out/(name+'-run.log')).write_text(r.stdout+r.stderr)
  expected=1 if mutant else 0
  if r.returncode!=expected:raise RuntimeError(name+' unexpected runtime exit '+str(r.returncode))
  if mutant and 'FAIL ' not in r.stdout:raise RuntimeError('mutant failed without an assertion')
  results.append({'name':name,'exit':r.returncode,'pass_assertions':r.stdout.count('PASS '),'failed_assertions':r.stdout.count('FAIL '),'expected_failure':mutant})
 run('identity',path=P/'scripts/ish-cleanup-tests/identity.c')
 run('production-token-signal',code=code)
 cancelled,cancel_manifest=b.c_unit(src,'static int ISHStartCancelledTask(')
 cancel_template=(P/'scripts/ish-cleanup-tests/cancelled_start.c').read_text(encoding="utf-8")
 run('production-cancelled-start',code=cancel_template.replace('/* PRODUCTION_CANCELLED_START */',cancelled))
 bad=cancelled.replace('task->group->doing_group_exit = true;', 'task->group->doing_group_exit = false;')
 if bad==cancelled:raise RuntimeError('exit-only mutation did not apply')
 run('mutant-cancelled-task-runs-guest',code=cancel_template.replace('/* PRODUCTION_CANCELLED_START */',bad),mutant=True)
 mutant=helper.replace('ISHExecutionIdentityEqual(token, task->group->fs_context)','ISHExecutionIdentitySession(token) == ISHExecutionIdentitySession(task->group->fs_context)')
 if mutant==helper:raise RuntimeError('identity mutation did not apply')
 run('mutant-ignore-generation',code=template.replace('/* PRODUCTION_TOKEN_SIGNAL */',mutant),mutant=True)
 mutant=helper.replace('pid < MAX_PID','pid <= MAX_PID')
 if mutant==helper:raise RuntimeError('out-of-range PID mutation did not apply')
 run('mutant-scan-exclusive-MAX-PID',code=template.replace('/* PRODUCTION_TOKEN_SIGNAL */',mutant),mutant=True)
 result={'scope':__doc__.splitlines()[0],'helper':manifest,'cancelled_start_helper':cancel_manifest,'header_sha256':hashlib.sha256(header.read_bytes()).hexdigest(),'runs':results,'swift_native':'not executed by this runner','ios_native':'not executed','ready_to_release':False}
 (out/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':
 try:main()
 except Exception as e:print('ERROR:',str(e),file=sys.stderr);sys.exit(2)
