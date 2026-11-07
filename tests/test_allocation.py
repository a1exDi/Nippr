import sys
sys.path.insert(0,'universal-bridge')
import bridge as b
import json,tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
with tempfile.TemporaryDirectory() as temp:
 root=Path(temp)
 def cmd(*args,token=None):return b.execute(b.parser().parse_args(['--project',temp]+(['--token',token] if token else [])+list(args)))
 cmd('init','--goal','Plan test');token=cmd('claim','--agent','codex')['token']
 rev=cmd('status')['revision'];cmd('reconcile','--revision',str(rev),'--evidence','Inspected',token=token)
 with b.database(temp) as db:
  for pane,agent in [('w1:p1','claude'),('w1:p2','grok')]:db.execute("INSERT INTO readiness VALUES('test',?,?, 'ready',?,'test fixture')",(pane,agent,__import__('time').time()))
 (root/'task.txt').write_text('Do assigned work')
 def entry(title,agent,path,pane):return {'title':title,'role':title,'assignee':agent,'resources':[path],'session':'test','pane':pane,'instructions_file':'task.txt'}
 plan=root/'plan.json'
 entries=[entry('UI','claude','src/ui','w1:p1'),entry('Tests','grok','tests','w1:p2')]
 mapping={'w1:p1':'claude','w1:p2':'grok'}
 def run(args,**kw):
  agent=mapping.get(args[args.index('get')+1]) if 'get' in args else None
  return SimpleNamespace(returncode=0,stdout=json.dumps({'result':{'agent':{'agent':agent}}}),stderr='')
 with patch.object(b.subprocess,'run',side_effect=run):
  plan.write_text(json.dumps({'assignments':entries}))
  before=cmd('status')['revision']
  result=cmd('allocate','--plan',str(plan),token=token)
  assert result['valid'] and not cmd('status')['tasks'] and cmd('status')['revision']==before
  # Last assignment conflicts; whole plan must roll back.
  entries[1]['resources']=['src/ui/button.py'];plan.write_text(json.dumps({'assignments':entries}))
  try:cmd('allocate','--plan',str(plan),'--send',token=token)
  except ValueError:pass
  else:raise AssertionError('Overlapping plan accepted')
  assert not cmd('status')['tasks']
  entries[1]['resources']=['tests'];entries[1]['pane']='w1:p1';entries[1]['assignee']='claude'
  plan.write_text(json.dumps({'assignments':entries}))
  try:cmd('allocate','--plan',str(plan),'--send',token=token)
  except ValueError:pass
  else:raise AssertionError('Two assignments on one pane accepted')
  assert not cmd('status')['tasks']
  entries[1]=entry('Tests','grok','tests','w1:p2');plan.write_text(json.dumps({'assignments':entries}))
  result=cmd('allocate','--plan',str(plan),'--send',token=token)
  assert result['committed'] and all(t['delivered'] for t in result['assignments'])
  assert len(cmd('status')['tasks'])==2
  queued=cmd('task-add','--title','Bypass','--assignee','muse',token=token)['id']
  try:cmd('task-update',queued,'--status','running','--result','Bypass',token=token)
  except ValueError:pass
  else:raise AssertionError('Started without reservations')
 print('PASS: plan validation, atomic rollback on overlap/busy pane, independent dispatch, no unreserved start')
