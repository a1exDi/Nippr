import sys
sys.path.insert(0,'universal-bridge')
import readiness as r
import bridge as b
import json,re,tempfile,time
from unittest.mock import patch
from types import SimpleNamespace
agents=[{'agent':'muse','pane_id':'w1:p1','agent_status':'idle','interactive_ready':True}]
marker=''
def run(args,**kw):
 global marker
 text=''
 if 'list' in args:text=json.dumps({'result':{'agents':agents}})
 elif 'prompt' in args:
  prompt=args[-1];suffix=re.search(r'suffix ([A-F0-9]+)',prompt)[1];marker='BRIDGE_READY_'+suffix
  assert marker not in prompt
 elif 'read' in args:text=marker
 return SimpleNamespace(returncode=0,stdout=text,stderr='')
with patch.object(r.subprocess,'run',side_effect=run):
 result=r.check_session('test',True)
 assert next(x for x in result if x['agent']=='muse')['status']=='ready'
 assert next(x for x in result if x['agent']=='codex')['status']=='not_running'
with tempfile.TemporaryDirectory() as temp:
 def cmd(*args,token=None):return b.execute(b.parser().parse_args(['--project',temp]+(['--token',token] if token else [])+list(args)))
 cmd('init','--goal','Health gate')
 token=cmd('claim','--agent','codex')['token'];rev=cmd('status')['revision']
 cmd('reconcile','--revision',str(rev),'--evidence','test',token=token)
 task=cmd('task-add','--title','Work','--assignee','muse',token=token)['id']
 try:cmd('task-grant',task,'--resources','a','--session','test','--pane','w1:p1',token=token)
 except ValueError as e:assert 'readiness' in str(e)
 else:raise AssertionError('Unchecked executor accepted')
 with patch.object(r,'check_session',return_value=result):cmd('health','--session','test','--probe')
 grant=cmd('task-grant',task,'--resources','a','--session','test','--pane','w1:p1',token=token)
 assert grant['worker_token']
 with b.database(temp) as db:
  db.execute('UPDATE readiness SET checked=?',(time.time()-400,))
  try:b.require_ready(db,'test','w1:p1','muse')
  except ValueError:pass
  else:raise AssertionError('Stale probe accepted')
print('PASS: unique response marker excludes echo, missing agents, persisted health, missing/stale readiness blocks dispatch')
