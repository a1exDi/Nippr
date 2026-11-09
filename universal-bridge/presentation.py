"""Display-only coordinator badge; never grants ownership."""
import json
import shutil
import sqlite3
import subprocess
import time
from project_store import state_dir
NAMES={'codex':'Codex','claude':'Claude','grok':'Grok','muse':'Muse','antigravity':'Antigravity'}

def sync(project):
    path=state_dir(project)/'state.sqlite3'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        from bridge import designated_coordinator
        designated=designated_coordinator(db)
        selected=db.execute("SELECT value FROM meta WHERE key='designated_session'").fetchone()
        lease=db.execute('SELECT agent,expires FROM lease WHERE id=1').fetchone()
        runtimes=[dict(r) for r in db.execute('SELECT * FROM runtimes')]
    leader=designated or (lease['agent'] if lease and (lease['expires'] or 0)>time.time() else None)
    matches=[r for r in runtimes if leader in json.loads(r['panes'])] if leader else []
    if selected and any(r['session']==selected[0] for r in matches):matches=[r for r in matches if r['session']==selected[0]]
    result=[];binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    for runtime in runtimes:
        for agent,pane in json.loads(runtime['panes']).items():
            base=[binary,'--session',runtime['session'],'pane','report-metadata',pane,
                  '--source','nippr:coordinator','--agent','agy' if agent=='antigravity' else agent]
            if agent==leader and len(matches)==1 and runtime['session']==matches[0]['session']:
                remaining=15000
                args=base+['--display-agent','👑 '+NAMES.get(agent,agent),'--ttl-ms',str(remaining)]
            else:args=base+['--clear-display-agent']
            try:
                p=subprocess.run(args,capture_output=True,text=True,timeout=2)
                result.append({'session':runtime['session'],'pane':pane,'ok':p.returncode==0})
            except (OSError,subprocess.TimeoutExpired):pass
    return result
