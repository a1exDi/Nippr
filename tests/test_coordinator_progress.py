import sys,json,tempfile,time,subprocess
sys.path.insert(0,'universal-bridge')
import bridge as b
import coordinator_progress as c
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp).resolve()
 def cmd(*args,token=None):return b.execute(b.parser().parse_args(['--project',str(root)]+(['--token',token] if token else [])+list(args)))
 cmd('init','--goal','Progress');claim=cmd('claim','--agent','codex')
 c.issue(root,'codex',claim['token'],claim['generation'])
 result=cmd('progress','--agent','codex','--current','Проверяю каталог','--done','Проверены ответы API','--next','Проверить браузер','--checks','10 тестов прошли')
 data=cmd('status')['coordinator_progress'][0]
 assert data['current_task']=='Проверяю каталог' and data['completed']=='Проверены ответы API'
 assert data['next_step']=='Проверить браузер' and result['ok']
 agents=[{'agent':'codex','pane_id':'p','agent_status':'working','cwd':str(root)}]
 assert c.nudge(root,'s',agents)=='fresh'
 with b.database(root) as db:db.execute('UPDATE coordinator_progress SET updated=?',(time.time()-100,))
 before=cmd('status')['coordinator_progress'][0]['updated']
 with patch.object(c.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='',stderr='')):
  assert c.nudge(root,'s',agents)=='reminded'
  assert c.nudge(root,'s',agents)=='throttled'
 assert cmd('status')['coordinator_progress'][0]['updated']==before
 cmd('handoff','--to','muse','--summary','Transfer','--next','Continue',token=claim['token'])
 try:cmd('progress','--agent','codex','--current','Old','--done','x','--next','y')
 except ValueError:pass
 else:raise AssertionError('Former owner can still report')
 print('PASS: structured report, internal capability, stale reminder throttle, no fake timestamp renewal, revoked owner rejected')
