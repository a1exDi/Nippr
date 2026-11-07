import sys
sys.path.insert(0,'universal-bridge')
import concurrent.futures
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('bridge','universal-bridge/bridge.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
class BridgeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=self.tmp.name
  self.call('init','--goal','Test objective')
 def tearDown(self):self.tmp.cleanup()
 def call(self,*args,token=None):
  prefix=['--project',self.root]+(['--token',token] if token else [])
  result=b.execute(b.parser().parse_args(prefix+list(args)))
  if args[0] in ('claim','recover'):
   state=self.call('status')
   self.call('reconcile','--revision',str(state['revision']),'--evidence','Test inspected state',token=result['token'])
  return result
 def expire(self):
  with sqlite3.connect(str(Path(self.root)/'.agent-bridge/state.sqlite3')) as db:
   db.execute('UPDATE lease SET expires=1 WHERE id=1')
 def test_handoff(self):
  a=self.call('claim','--agent','claude')['token']
  task=self.call('task-add','--title','Review','--assignee','codex',token=a)['id']
  self.call('task-update',task,'--status','review','--result','Diff available',token=a)
  self.call('handoff','--to','grok','--summary','Ready','--next','Check diff',token=a)
  with self.assertRaises(ValueError):self.call('heartbeat',token=a)
  with self.assertRaises(ValueError):self.call('claim','--agent','muse')
  with self.assertRaises(ValueError):self.call('recover','--agent','muse','--reason','early')
  c=self.call('claim','--agent','grok')['token'];self.assertNotEqual(c,a)
  state=self.call('context');self.assertEqual(state['tasks'][0]['status'],'review')
  self.assertNotIn(a,str(state));self.assertNotIn(c,str(state))
 def test_expiry(self):
  a=self.call('claim','--agent','codex')['token'];self.expire()
  with self.assertRaises(ValueError):self.call('heartbeat',token=a)
  new=self.call('recover','--agent','antigravity','--reason','Limit reached')
  with self.assertRaises(ValueError):self.call('release','--summary','Old',token=a)
  self.call('checkpoint','--summary','Resumed','--next','Continue',token=new['token'])
 def test_race(self):
  def claim(agent):
   try:self.call('claim','--agent',agent);return True
   except ValueError:return False
  with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
   self.assertEqual(sum(pool.map(claim,b.AGENTS)),1)
 def test_idempotent_init(self):
  a=self.call('claim','--agent','muse')['token']
  self.call('init','--goal','Do not replace')
  self.assertEqual(self.call('status')['goal'],'Test objective')
  self.call('heartbeat',token=a)
 def test_unknown_task(self):
  token=self.call('claim','--agent','grok')['token']
  with self.assertRaises(ValueError):self.call('task-update','missing','--status','done','--result','x',token=token)
  self.assertEqual(self.call('status')['tasks'],[])
 def test_worker_survives_switch(self):
  a=self.call('claim','--agent','claude')['token']
  task=self.call('task-add','--title','Edit A','--assignee','muse',token=a)['id']
  with b.database(self.root) as db:
   db.execute("INSERT OR REPLACE INTO readiness VALUES('test','w1:p3','muse','ready',?,'test fixture')",(__import__('time').time(),))
  worker=self.call('task-grant',task,'--resources','src/a.py','--session','test','--pane','w1:p3',token=a)['worker_token']
  self.call('switch','--to','codex','--reason','User switches coordinator')
  self.call('worker-report',task,'--status','running','--result','Half complete','--next','Finish',token=worker)
  new=self.call('claim','--agent','codex')['token']
  with self.assertRaises(ValueError):self.call('heartbeat',token=a)
  other=self.call('task-add','--title','Conflicting edit','--assignee','grok',token=new)['id']
  with self.assertRaises(ValueError):self.call('task-grant',other,'--resources','src',token=new)
  with self.assertRaises(ValueError):self.call('task-update',task,'--status','cancelled','--result','Cancel',token=new)
  with self.assertRaises(ValueError):self.call('worker-report',other,'--status','review','--result','Fake','--next','x',token=worker)
  self.call('worker-report',task,'--status','review','--result','Diff ready','--next','Validate',token=worker)
  self.call('task-update',task,'--status','done','--result','Validated',token=new)
  self.call('task-grant',other,'--resources','src',token=new)
  state=self.call('status');self.assertNotIn(worker,str(state))
 def test_reconcile_revision(self):
  token=self.call('claim','--agent','grok')['token']
  before=self.call('status')['revision']
  self.call('checkpoint','--summary','New state','--next','Continue',token=token)
  with self.assertRaises(ValueError):self.call('reconcile','--revision',str(before),'--evidence','Stale',token=token)
if __name__=='__main__':unittest.main()
