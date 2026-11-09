import sys
sys.path.insert(0,'universal-bridge')
import bridge as b
from project_store import state_dir
import tempfile,subprocess,sqlite3,json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
with tempfile.TemporaryDirectory() as temp:
 root=(Path(temp)/'repo').resolve();root.mkdir();other=Path(temp)/'worktree'
 def git(*args):subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True)
 git('init');git('config','user.email','test@example.invalid');git('config','user.name','Test')
 (root/'a').write_text('a');git('add','a');git('commit','-m','initial');git('worktree','add',str(other),'-b','other')
 def cmd(project,*args,token=None):return b.execute(b.parser().parse_args(['--project',str(project)]+(['--token',token] if token else [])+list(args)))
 cmd(root,'init','--goal','Shared')
 assert state_dir(root)==state_dir(other)
 token=cmd(root,'claim','--agent','claude')['token']
 try:cmd(other,'claim','--agent','grok')
 except ValueError:pass
 else:raise AssertionError('Worktree split ownership')
 revision=cmd(root,'status')['revision'];cmd(root,'reconcile','--revision',str(revision),'--evidence','checked',token=token)
 for project,session in [(root,'one'),(other,'two')]:cmd(project,'register-session','--session',session,'--panes','{"muse":"w1:p2"}')
 assert len(cmd(root,'status')['sessions'])==2
 backup=Path(cmd(other,'backup')['backup'])
 with sqlite3.connect(backup) as db:assert db.execute('SELECT COUNT(*) FROM runtimes').fetchone()[0]==2
 with b.database(root) as db:db.execute("INSERT INTO readiness VALUES('one','w1:p2','muse','ready',?,'test fixture')",(__import__('time').time(),))
 instructions=root/'task.txt';instructions.write_text('Inspect a; do not edit')
 calls=[]
 def run(args,**kw):
  calls.append(args)
  return SimpleNamespace(returncode=0,stdout=json.dumps({'result':{'agent':{'agent':'muse'}}}),stderr='')
 canonical=state_dir(root)
 with patch.object(b.subprocess,'run',side_effect=run),patch.object(b,'state_dir',return_value=canonical):
  # database uses patched path but project validation remains real
  result=cmd(root,'dispatch','--title','Inspect','--assignee','muse','--resources','a','--session','one','--pane','w1:p2','--instructions-file',str(instructions),token=token)
 assert result['delivered']
 private=json.loads(Path(result['assignment_file']).read_text())
 assert 'worker_token' in private and 'worker_token' not in result
 prompts=[call[-1] for call in calls if 'prompt' in call]
 assert any(f'report {result["id"]} --status running' in prompt for prompt in prompts)
 assert all(private['worker_token'] not in prompt for prompt in prompts)
 print('PASS: shared worktree ownership, multiple sessions, consistent backup, registered dispatch and private worker assignment')
with tempfile.TemporaryDirectory() as temp:
 root=(Path(temp)/'repo').resolve();root.mkdir()
 subprocess.run(['git','-C',str(root),'init'],check=True,capture_output=True)
 # Build a legacy ledger outside Git and place it at its historical location.
 plain=Path(temp)/'plain';plain.mkdir()
 b.execute(b.parser().parse_args(['--project',str(plain),'init','--goal','Legacy']))
 (plain/'.agent-bridge').rename(root/'.agent-bridge')
 assert state_dir(root)==root/'.agent-bridge'
 try:b.execute(b.parser().parse_args(['--project',str(root),'init','--goal','Wrong']))
 except ValueError:pass
 else:raise AssertionError('Unsafe live migration allowed')
 result=b.execute(b.parser().parse_args(['--project',str(root),'migrate','--offline']))
 assert result['migrated'] and (state_dir(root)/'state.sqlite3').exists()
 state=b.execute(b.parser().parse_args(['--project',str(root),'status']))
 assert state['goal']=='Legacy'
print('PASS: explicit offline migration preserves legacy goal and state')
