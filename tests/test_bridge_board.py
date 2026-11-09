import sys
sys.path.insert(0,'universal-bridge')
import importlib.util
import tempfile
from pathlib import Path
import json

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
b=load('bridge','universal-bridge/bridge.py')
board=load('board','usage-monitor/bridge-board.py')
with tempfile.TemporaryDirectory() as temp:
 def cmd(*args,token=None):
  prefix=['--project',temp]+(['--token',token] if token else [])
  return b.execute(b.parser().parse_args(prefix+list(args)))
 assert 'error' in board.read_state(temp)
 assert not (Path(temp)/'.agent-bridge').exists()
 cmd('init','--goal','Test board')
 owner=cmd('claim','--agent','claude')['token']
 revision=cmd('status')['revision']
 cmd('reconcile','--revision',str(revision),'--evidence','test',token=owner)
 task=cmd('task-add','--title','Implement search','--assignee','muse',token=owner)['id']
 with b.database(temp) as db:db.execute("INSERT INTO readiness VALUES('test','w1:p2','muse','ready',?,'test fixture')",(__import__('time').time(),))
 worker=cmd('task-grant',task,'--resources','src/search.py','--pane','w1:p2','--session','test',token=owner)['worker_token']
 cmd('worker-report',task,'--status','running','--result','Implementing filter','--next','Validate',token=worker)
 first=board.read_state(temp)
 assert 'Claude' in board.coordinator_line(first)
 assert 'Implement search' in str(board.cards(first,{}))
 assert owner not in json.dumps(first) and worker not in json.dumps(first)
 cmd('switch','--to','codex','--reason','Test transfer')
 assert 'Codex' in board.coordinator_line(board.read_state(temp))
 cmd('claim','--agent','codex')
 after=board.read_state(temp)
 assert 'Codex' in board.coordinator_line(after)
 assert after['tasks'][0]['status']=='running'
 assert after['workers'][task]['pane']=='w1:p2'
 assert 'Implementing filter' in str(board.cards(after,{}))
print('PASS: live coordinator transfer, surviving worker reports, pane bindings, token redaction, read-only missing state')

from unittest.mock import patch
class Screen:
 def keypad(self,*a):pass
 def timeout(self,*a):pass
 def getmaxyx(self):return 30,100
 def erase(self):pass
 def addstr(self,*a):pass
 def refresh(self):pass
 def getch(self):return ord('q')
with patch.object(board.time,'monotonic',return_value=0), patch.object(board,'read_state',return_value=after), patch.object(board,'process_states',return_value={}), patch.object(board.curses,'has_colors',return_value=False), patch.object(board.curses,'curs_set'):
 board.board(Screen(),Path('/test'))
print('PASS: board renders its first frame when monotonic starts at zero')

fixture={'lease':{'agent':'codex','expires':__import__('time').time()+100,'target':None},'tasks':[
 {'id':'a','assignee':'muse','title':'Current feature','status':'running','result':'Implemented input','updated':3},
 {'id':'b','assignee':'muse','title':'Completed feature','status':'done','result':'Tests passed','updated':2},
 {'id':'c','assignee':'muse','title':'Await validation','status':'review','result':'Unverified proposal','updated':1}],
 'workers':{'a':{'report_at':__import__('time').time()}},'reports':{'a':{'next':'Run tests'}},'readiness':[]}
view=board.agent_view(fixture,{'muse':{'status':'working @ session'}},'muse')
assert view['current']['id']=='a' and view['recent']['id']=='b'
assert view['next']=='Run tests' and view['summary']=='Implemented input'
assert len(view['completed'])==1 and 'c' not in [t['id'] for t in view['completed']]
assert board.agent_view(fixture,{},'codex')['leader']
print('PASS: current work, next step, completed results and pending review remain distinct')
