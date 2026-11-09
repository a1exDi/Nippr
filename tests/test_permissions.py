import sys
sys.path.insert(0,'universal-bridge')
import bridge as b
import permissions as p
import tempfile,json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp).resolve()
 def cmd(*args,token=None):return b.execute(b.parser().parse_args(['--project',str(root)]+(['--token',token] if token else [])+list(args)))
 cmd('init','--goal','Permissions');token=cmd('claim','--agent','codex')['token']
 rev=cmd('status')['revision'];cmd('reconcile','--revision',str(rev),'--evidence','test',token=token)
 task=cmd('task-add','--title','A','--assignee','antigravity',token=token)['id']
 with b.database(root) as db:db.execute("INSERT INTO readiness VALUES('s','p','antigravity','ready',?,'test')",(__import__('time').time(),))
 worker=cmd('task-grant',task,'--resources','src/a.py','--session','s','--pane','p',token=token)['worker_token']
 calls=[];command_request=None;requested=str(Path(p.__file__).with_name('AGENT_BRIDGE.md'))
 def run(args,**kw):
  calls.append(args)
  if 'list' in args:out=json.dumps({'result':{'agents':[{'agent':'agy','pane_id':'p','cwd':str(root)}]}})
  elif 'read' in args and command_request:out=f'Command\nRequesting permission for:\n {command_request}\nRun this command?\n> 1. Yes, run command\n 2. Yes, and always allow'
  elif 'read' in args:out=f'File access\n────\nRead:\n{requested}\nReason: outside workspace\nAllow access to this file?\n> 1. Yes, allow access\n  2. Yes, and always allow non-workspace access'
  else:out='{}'
  return SimpleNamespace(returncode=0,stdout=out,stderr='')
 with patch.object(p.subprocess,'run',side_effect=run):
  assert p.control(root,'s',worker,task)[0]['action']=='one_file_read_approved'
  requested='/outside/secret.txt';calls.clear()
  assert p.control(root,'s',token)[0]['action']=='outside_assignment_scope'
  assert not any('send-keys' in x for x in calls)
 # Service commands are narrow; arbitrary shell execution is never approved.
 with patch.object(p.subprocess,'run',side_effect=run):
  command_request='which agent-bridge';calls.clear()
  assert p.control(root,'s',token)[0]['action']=='scoped_command_approved'
  command_request='rm -rf src';calls.clear()
  assert p.control(root,'s',token)[0]['action']=='command_needs_coordinator_review'
  assert not any('send-keys' in x for x in calls)
print('PASS: service command allowed; unrelated destructive command untouched')
print('PASS: worker and coordinator can control scoped reads; unknown files never approved; no always-allow selection')
