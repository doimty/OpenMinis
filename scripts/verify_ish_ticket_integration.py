#!/usr/bin/env python3
"""Static integration gates only. These are NOT Swift/iOS compilation."""
import argparse,json,pathlib,re,subprocess,sys
from tree_sitter import Language,Parser
import tree_sitter_swift
P=pathlib.Path(__file__).resolve().parents[1]
def walk(n):
 yield n
 for c in n.children:yield from walk(c)
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',required=True,type=pathlib.Path);args=ap.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=False)
 parser=Parser(Language(tree_sitter_swift.language()));checks=[]
 def check(name,ok):
  checks.append({'name':name,'ok':bool(ok)})
  if not ok:raise RuntimeError(name)
 swift=['src/ios/Agent/ISH/ISHExecutionCoordinator.swift','src/ios/Agent/ISH/ISHCommandLifecycle.swift','src/ios/Agent/ISH/ISHTerminalShellLifecycle.swift','src/ios/Agent/ISH/MinisFsRouter.swift','src/ios/Views/Rootfs/ISHTerminalView.swift','src/ios/Debug/DebugJSONRPC.swift','scripts/ish-lifecycle-tests/main.swift','scripts/ish-terminal-tests/main.swift','src/ios/Agent/Chat/ChatSearchQuery.swift','src/ios/Views/Chat/ChatSearchView.swift','scripts/chat-search-tests/main.swift']
 for fn in swift:
  raw=(P/fn).read_bytes();tree=parser.parse(raw)
  if fn.endswith('DebugJSONRPC.swift'):
   # Existing dictionary/coalescing syntax exceeds this grammar; require the
   # exact error-byte signatures to remain unchanged, not a fictitious clean parse.
   baseline=subprocess.check_output(['git','-C',str(P),'show','HEAD:'+fn])
   def errors(t,b):return [(n.type,b[n.start_byte:n.end_byte].decode()) for n in walk(t.root_node) if n.type=='ERROR' or n.is_missing]
   check('Debug AST no new error signatures (baseline grammar errors remain)',errors(tree,raw)==errors(parser.parse(baseline),baseline))
  else:check('Swift AST '+fn,not tree.root_node.has_error)
 c=(P/'src/ios/Agent/ISH/ISHExecutionCoordinator.swift').read_text();life=(P/'src/ios/Agent/ISH/ISHCommandLifecycle.swift').read_text();router=(P/'src/ios/Agent/ISH/MinisFsRouter.swift').read_text();backend=(P/'src/ios/iSH/ISHShellExecutor.m').read_text();hook=(P/'src/ios/iSH/ISHKernel.m').read_text();project=(P/'src/ios/Minis.xcodeproj/project.pbxproj').read_text()
 check('caller has no PID cleanup API',not any(s in c for s in ['killProcessGroup(', 'finalizeTimedOutPid(', 'killProcess(']))
 check('nonisolated execution entry', 'nonisolated func execute(' in c)
 check('entry cancellation checked before reservation',c.index('try Task.checkCancellation()') < c.index('guard Self.reserveExecution'))
 check('post-completion cancellation preserves a completed result','if wasCancelled && Task.isCancelled { throw CancellationError() }' in c)
 check('lifecycle marks only explicit cancellation as cancelled','wasCancelled: !timedOut' in life and 'wasCancelled: false' in life)
 check('lifecycle native harness asserts terminal cancellation class','wasCancelled' in (P/'scripts/ish-lifecycle-tests/main.swift').read_text())
 ui=(P/'src/ios/Agent/Chat/AIChatViewModel+BackgroundTask.swift').read_text()
 check('Stop log reports requests not confirmed signals', 'requested cancellation for' in ui and '— signalled' not in ui)
 check('deadline installed before detached actor preparation', c.index('state.armDeadline()') < c.index('await prepareCommand'))
 check('bounded launch and durable reservations', 'maxConcurrentOperationCount = 2' in c and 'maxExecutionReservations = 32' in c and 'defer { Self.pipelineFinished' in c)
 check('main actor output delivery preserved','DispatchQueue.main.async' in c and 'guard !state.isTerminal' in c)
 check('original stdin shell framing preserved', 'cd /root\\n({ exec 0</dev/null; } 2>/dev/null || true;' in c)
 check('route strips generation', 'context & UInt64(UInt32.max)' in router and 'nextContext <=' in router and 'nextContext &+=' not in router)
 check('lifecycle is compiled source',project.count('A45020261020000000000001')==2 and project.count('A45020261020000000000002')==3)
 check('exit event includes generation before dispatch', '"executionToken"' in hook and 'fs_context' in hook)
 check('backend checks exact inherited execution identity','ISHExecutionIdentityEqual(token, task->group->fs_context)' in backend)
 check('token sweep stays below exclusive MAX_PID','for (int pid = 2; pid < MAX_PID; pid++)' in backend)
 check('backend has explicit bounded cleanup', 'ISHShellCleanupCapacity = 64' in backend and 'ISHShellCleanupMaxAttempts = 20' in backend)
 debug=(P/'src/ios/Debug/DebugJSONRPC.swift').read_text()
 check('debug session route allocation failure fails closed','if sessionId != nil && fsContext == 0' in debug and 'nothing was launched.' in debug)
 # Verify the deadline uses a global queue rather than delivery/cleanup queue.
 check('deadline independent of kernel queue',re.search(r'DispatchQueue.global\([^\n]*\)\.asyncAfter',life) is not None)
 terminal=(P/'src/ios/Views/Rootfs/ISHTerminalView.swift').read_text();termlife=(P/'src/ios/Agent/ISH/ISHTerminalShellLifecycle.swift').read_text()
 check('terminal shell launches under an execution identity','executionToken: token' in terminal and 'ISHKernel.shared.executeCommand(["/bin/sh", "-l"])' not in terminal)
 check('terminal shell is single flight','guard shellLifecycle.beginLaunch(token: token) else { return false }' in terminal)
 check('terminal stop reclaims by identity','requestStop()' in terminal and 'requestTermination(ticket, error: .cancelled)' in terminal)
 check('terminal launch watchdog abandons identity','isAwaitingPID(token: token)' in terminal and 'requestTermination(ticket, error: .timeout)' in terminal)
 check('terminal lifecycle rejects stale launch reports','guard case .launching(let expected) = phase, expected == token else { return false }' in termlife)
 check('terminal harness covers watchdog and restart','isAwaitingPID' in (P/'scripts/ish-terminal-tests/main.swift').read_text() and 'restart uses a fresh token' in (P/'scripts/ish-terminal-tests/main.swift').read_text())
 check('kernel stamps the terminal task identity','launchTask->group->fs_context = executionToken' in hook)
 check('kernel releases an abandoned launch without running','launchTask->group->doing_group_exit = true' in hook and 'report(0, -4)' in hook)
 check('cancelled tokens outlive their cleanup pass','isTerminationRequestedForToken' in backend and 'rememberCancelledToken' in backend)
 check('terminal lifecycle is compiled source',project.count('A45020261020000000000003')==2 and project.count('A45020261020000000000004')==3)
 searchview=(P/'src/ios/Views/Chat/ChatSearchView.swift').read_text();chatview=(P/'src/ios/Views/Chat/AIChatView.swift').read_text();searchquery=(P/'src/ios/Agent/Chat/ChatSearchQuery.swift').read_text();listv3=(P/'src/ios/Agent/MessageList/CollectionViewMessageListV3.swift').read_text()
 check('chat search queries this conversation','ChatStore.shared.searchMessages' in searchview and 'sessionIds: [sessionId]' in searchview)
 check('chat search debounces keystrokes','Task.sleep(nanoseconds: 250_000_000)' in searchview)
 check('chat search jumps to the picked message','chatJumpRequest = ChatJumpRequest(messageID: messageId, nonce: chatJumpNonce)' in chatview)
 check('chat search entry lives in the trailing menu','AppLocalized("Search in Chat")' in chatview and 'onSearch: { showChatSearch = true }' in chatview)
 check('transcript jumps by message id','request.messageID' in listv3 and 'scrollToItem(at: indexPath, at: .centeredVertically' in listv3)
 check('jump flash sits behind the cells','cv.insertSubview(marker, at: 0)' in listv3)
 check('search keywords are bounded','static let maxKeywords = 6' in searchquery)
 check('search files are compiled sources',project.count('A45020261020000000000005')==2 and project.count('A45020261020000000000007')==2)


 (out/'results.json').write_text(json.dumps({'scope':__doc__,'checks':checks,'swift_native':'NOT RUN','ios_build':'NOT RUN','ready_to_release':False},indent=2))
 print('STATIC_INTEGRATION_CHECKS',len(checks),'PASS; NOT native Swift or iOS')
if __name__=='__main__':
 try:main()
 except Exception as e:print('ERROR:',e,file=sys.stderr);sys.exit(1)
