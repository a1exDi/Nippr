"""Scoped file-read approvals for recognized Antigravity dialogs."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import secrets
import shutil
import sqlite3
import subprocess
from project_store import state_dir


def control(project,session,token,worker_task=None):
    from bridge import database,owner,event
    with database(project) as db:
        if worker_task:
            worker=db.execute('SELECT * FROM workers WHERE task=?',(worker_task,)).fetchone()
            import secrets
            if not worker or not token or not secrets.compare_digest(token,worker['token']):raise ValueError('Invalid worker capability')
        else:lease=owner(db,token)
        rows=[dict(r) for r in db.execute("SELECT workers.*,tasks.assignee,tasks.status FROM workers JOIN tasks ON tasks.id=workers.task WHERE tasks.status IN ('running','blocked') AND workers.session=?",(session,))]
        if worker_task:rows=[r for r in rows if r['task']==worker_task]
        else:
            runtime=db.execute('SELECT panes FROM runtimes WHERE session=?',(session,)).fetchone()
            pane=json.loads(runtime[0]).get(lease['agent']) if runtime else None
            if pane and not any(r['pane']==pane for r in rows):
                rows.append({'task':'coordinator','pane':pane,'assignee':lease['agent'],'resources':'[]','token':token,'coordinator':True})
    binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    base=[binary,'--session',session]
    def run(*args):return subprocess.run(base+list(args),capture_output=True,text=True,timeout=5)
    listed=run('agent','list')
    agents={a['pane_id']:a for a in json.loads(listed.stdout)['result']['agents']}
    results=[]
    for worker in rows:
        pane=worker['pane'];a=agents.get(pane,{})
        result={'task':worker['task'],'pane':pane,'action':'no_known_request'}
        if a.get('agent')!='agy':
            result['action']='unsupported_or_changed_agent';results.append(result);continue
        cwd=a.get('foreground_cwd') or a.get('cwd')
        if not cwd or state_dir(cwd)!=state_dir(project):
            result['action']='project_mismatch';results.append(result);continue
        screen=run('agent','read',pane,'--source','visible').stdout
        match=re.search(r'File access\s*[─\s]*\nRead:\s*\n(.*?)\nReason:',screen,re.S)
        if match and 'Allow access to this file?' in screen and '> 1. Yes, allow access' in screen:
            raw=''.join(line.strip() for line in match[1].splitlines())
            requested=Path(raw)
            fixed={Path(__file__).with_name('AGENT_BRIDGE.md').resolve(),
                   (state_dir(project)/'assignments'/(worker['task']+'.json')).resolve()}
            local=Path(cwd)/'.nippr-runtime'/worker['task']
            fixed.update({(local/'AGENT_BRIDGE.md').resolve(),(local/'assignment.json').resolve()})
            allowed=requested.is_absolute() and requested.resolve() in fixed
            if not allowed and requested.is_absolute():
                from project_store import git
                checkout=git(cwd,'rev-parse','--show-toplevel')
                root=Path(checkout or cwd).resolve()
                try:relative=requested.resolve().relative_to(root).as_posix()
                except ValueError:relative=None
                allowed=relative is not None and any(relative==r or relative.startswith(r+'/') for r in json.loads(worker['resources']))
            if allowed:
                # Re-read immediately; do not send Enter if the dialog changed.
                if run('agent','read',pane,'--source','visible').stdout==screen:
                    sent=run('agent','send-keys',pane,'enter')
                    result['action']='one_file_read_approved' if sent.returncode==0 else 'delivery_unconfirmed'
                else:result['action']='dialog_changed'
            else:result['action']='outside_assignment_scope'
        command_match=re.search(r'Requesting permission for:\s*\n(.*?)\nRun this command\?',screen,re.S)
        if command_match and '> 1. Yes, run command' in screen:
            command_text=' '.join(line.strip() for line in command_match[1].splitlines())
            allowed_command=command_text in ('which agent-bridge','command -v agent-bridge','pwd','agent-bridge --help','agent-bridge worker-report --help')
            if not allowed_command and not re.search(r'[;&|<>`$()\n]',command_text):
                try:
                    import contextlib,io
                    from bridge import parser
                    argv=shlex.split(command_text)
                    script=Path(__file__).with_name('bridge.py').resolve()
                    if argv and argv[0] in ('python3','/usr/bin/python3') and len(argv)>1 and Path(argv[1]).resolve()==script:
                        argv=argv[2:]
                    elif argv and argv[0] in ('agent-bridge',str(Path.home()/'.local/bin/agent-bridge')):
                        argv=argv[1:]
                    else:argv=[]
                    with contextlib.redirect_stderr(io.StringIO()):
                        parsed=parser().parse_args(argv)
                    allowed_command=(parsed.command=='progress' and worker.get('coordinator') and parsed.agent==worker['assignee'] and state_dir(parsed.project)==state_dir(project)) or (parsed.command in ('worker-report','report') and parsed.id==worker['task']
                        and (parsed.command=='report' or (parsed.token and secrets.compare_digest(parsed.token,worker['token'])))
                        and state_dir(parsed.project)==state_dir(project))
                except (SystemExit,ValueError,OSError):allowed_command=False
            if allowed_command and run('agent','read',pane,'--source','visible').stdout==screen:
                sent=run('agent','send-keys',pane,'enter')
                result['action']='scoped_command_approved' if sent.returncode==0 else 'delivery_unconfirmed'
            else:result['action']='command_needs_coordinator_review'
        if result['action']!='no_known_request':
            with database(project) as db:
                previous=db.execute("SELECT body FROM events WHERE kind='permission-control' ORDER BY id DESC LIMIT 1").fetchone()
                if not previous or json.loads(previous[0])!=result:
                    event(db,None,'permission-control',result)
        results.append(result)
    return results
