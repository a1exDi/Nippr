import sys
sys.path.insert(0,'universal-bridge')
import tempfile,json,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import bridge as b
import workspace_trust as trust
import presentation
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp).resolve()
 a=b.parser().parse_args(['--project',str(root),'init','--goal','Test']);b.execute(a)
 agents=[{'agent':'claude','pane_id':'p1','cwd':str(root)}, {'agent':'muse','pane_id':'p2','cwd':'/elsewhere'}]
 calls=[]
 def run(args,**kwargs):
  calls.append(args)
  if 'list' in args:out=json.dumps({'result':{'agents':agents}})
  elif 'read' in args:out=f'Accessing workspace:\n{root}\nClaude Code read, edit, and execute files\n❯ No, exit\n Yes, I trust this folder'
  else:out='{}'
  return SimpleNamespace(returncode=0,stdout=out,stderr='')
 with patch.object(trust.subprocess,'run',side_effect=run):
  result=trust.trust(root,'s')
  assert result[0]['action']=='confirmation_sent' and result[1]['action']=='skipped'
  assert len([x for x in calls if 'send-keys' in x])==1
  assert calls[-1][-2:]!=['yes','enter']
 def cmd(*args):return b.execute(b.parser().parse_args(['--project',str(root)]+list(args)))
 cmd('register-session','--session','s','--panes','{"claude":"p1"}')
 cmd('claim','--agent','claude')
 calls.clear()
 with patch.object(presentation.subprocess,'run',side_effect=run):presentation.sync(root)
 assert any('👑 Claude' in x for x in calls)
 assert all('report-metadata' in x for x in calls if '--session' in x)
print('PASS: exact trust dialog and path, unknown folders untouched, display-only active crown')
