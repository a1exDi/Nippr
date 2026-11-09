import sys,json,tempfile,importlib.util
sys.path.insert(0,'universal-bridge')
import bridge as b
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('board','usage-monitor/bridge-board.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as temp:
 root=Path(temp).resolve()
 def cmd(*args,token=None):return b.execute(b.parser().parse_args(['--project',str(root)]+(['--token',token] if token else [])+list(args)))
 cmd('init','--goal','Switch test');token=cmd('claim','--agent','claude')['token']
 revision=cmd('status')['revision'];cmd('reconcile','--revision',str(revision),'--evidence','Checked',token=token)
 task=cmd('task-add','--title','Keep task','--assignee','muse',token=token)['id']
 actual={'agent':'codex','interactive_ready':True,'agent_status':'idle','cwd':str(root)}
 def run(args,**kwargs):return SimpleNamespace(returncode=0,stdout=json.dumps({'result':{'agent':actual}}),stderr='')
 with patch.object(m.subprocess,'run',side_effect=run),patch.object(b,'notify_coordinator',return_value='Request delivered'),patch('presentation.sync',return_value=[]):
  result=m.panel_switch(root,'codex','s','p',1,'claude')
  assert result['tasks_preserved'] and cmd('status')['tasks'][0]['id']==task
  assert cmd('status')['designated_coordinator']=='codex'
  try:m.panel_switch(root,'muse','s','p',1,'claude')
  except ValueError:pass
  else:raise AssertionError('Wrong occupant accepted')
  try:m.panel_switch(root,'codex','s','p',1,'claude')
  except ValueError:pass
  else:raise AssertionError('Changed coordinator ignored')
  actual['agent_status']='blocked'
  try:m.panel_switch(root,'codex','s','p',1,'codex')
  except ValueError:pass
  else:raise AssertionError('Blocked target accepted')
class Screen:
 def __init__(self,key):self.key=key
 def getmaxyx(self):return 40,120
 def addnstr(self,*a):pass
 def addstr(self,*a):pass
 def refresh(self):pass
 def getch(self):return self.key
state={'designated_coordinator':'claude','lease':{'agent':'claude','generation':1},'sessions':[{'session':'s','panes':{'codex':'p'}}]}
assert m.switch_dialog(Screen(27),Path('/test'),state,'codex')[0] is None
assert m.switch_dialog(Screen(10),Path('/test'),state,'codex')[0][1:4]==('codex','s','p')
print('PASS: panel switch preserves tasks; rejects wrong/blocked target and changed leadership; confirm/cancel dialog')
