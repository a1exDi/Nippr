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
