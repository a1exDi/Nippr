"""Structured, generation-bound coordinator progress and throttled reporting reminders."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import time
from project_store import state_dir


def issue(project,agent,token,generation):
    folder=state_dir(project)/'capabilities';folder.mkdir(mode=0o700,exist_ok=True)
    dest=folder/('coordinator-'+agent+'.json');temp=folder/(dest.name+'.'+str(os.getpid())+'.tmp')
    with temp.open('w') as f:
        os.chmod(temp,0o600);json.dump({'agent':agent,'generation':generation,'token':token},f)
    os.replace(temp,dest)


def record(args):
    from bridge import database,owner,event
    token=args.token or os.environ.get('BRIDGE_TOKEN')
    if not token:
        credential=state_dir(args.project)/'capabilities'/('coordinator-'+args.agent+'.json')
        try:token=json.loads(credential.read_text())['token']
        except (OSError,ValueError,KeyError):raise ValueError('Claim coordinator role first; progress capability is unavailable')
    with database(args.project) as db:
        lease=owner(db,token)
        if lease['agent']!=args.agent:raise ValueError('Only the current coordinator can submit this progress')
        at=time.time()
        db.execute('INSERT OR REPLACE INTO coordinator_progress VALUES(?,?,?,?,?,?,?,?)',
                   (args.agent,lease['generation'],args.current,args.done,args.next,args.state,args.checks,at))
        # A real progress report proves the coordinator is active; an expired lease is never revived.
        db.execute('UPDATE lease SET expires=? WHERE id=1',(max(lease['expires'],at+900),))
        event(db,args.agent,'coordinator-progress',{'current':args.current,'done':args.done,'next':args.next,
            'state':args.state,'checks':args.checks,'generation':lease['generation']})
    return {'ok':True,'agent':args.agent,'state':args.state,'updated':at}


def nudge(project,session,agents):
    from bridge import database,designated_coordinator,event
    with database(project) as db:
        leader=designated_coordinator(db)
        if not leader:return 'no_coordinator'
        expected='agy' if leader=='antigravity' else leader
        candidates=[a for a in agents if a.get('agent')==expected]
        if len(candidates)!=1:return 'ambiguous_or_missing'
        target=candidates[0]
        if target.get('agent_status')!='working':return 'not_working'
        cwd=target.get('foreground_cwd') or target.get('cwd')
        if not cwd or state_dir(cwd)!=state_dir(project):return 'different_project'
        lease=db.execute('SELECT * FROM lease WHERE id=1').fetchone()
        if lease['agent'] and lease['agent']!=leader and (lease['expires'] or 0)>time.time():return 'different_owner'
        progress=db.execute('SELECT * FROM coordinator_progress WHERE agent=? AND generation=?',(leader,lease['generation'])).fetchone()
        if progress and time.time()-progress['updated']<60:return 'fresh'
        key='progress_reminder:'+session+':'+leader+':'+str(lease['generation'])
        row=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        saved=json.loads(row[0]) if row else {'at':0,'count':0,'progress_at':None}
        progress_at=progress['updated'] if progress else None
        if progress_at!=saved.get('progress_at'):saved['count']=0
        if time.time()-saved['at']<120:return 'throttled'
        if saved['count']>=3:return 'awaiting_report'
        # Herdr can retain a working badge after the Codex connection has failed.
        # Never append reminders to an unrecovered client or an existing reminder draft.
        binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
        try:
            screen=subprocess.run([binary,'--session',session,'agent','read',target['pane_id'],
                '--source','visible'],capture_output=True,text=True,timeout=3)
            if screen.returncode or not screen.stdout.strip():return 'surface_unconfirmed'
            text=screen.stdout.lower()
            if any(marker in text for marker in ('automatic reconnect','reconnect failed','connecting to the server')):
                return 'connection_blocked'
            if 'nippr progress reporting reminder' in text:return 'reminder_pending'
            if (target.get('composer') or {}).get('state') in ('editing','nonempty','draft'):
                return 'draft_pending'
        except (OSError,subprocess.TimeoutExpired):return 'surface_unconfirmed'
        # Reserve reminder atomically before sending; another guard cannot duplicate it.
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps({'at':time.time(),'count':saved['count']+1,'progress_at':progress_at})))
    script=Path(__file__).with_name('bridge.py')
    prompt=(f'Nippr progress reporting reminder for the current coordinator {leader}. '
        'Do not cancel or change the current user task. At your next safe tool boundary, '
        'record factual progress in Russian: current task, completed work, next step, checks. '
        f'Use python3 "{script}" --project "{Path(project).resolve()}" progress --agent {leader} '
        '--current "Текущая задача" --done "Что реально сделано" --next "Следующий шаг" --checks "Фактические проверки" --state working. '
        'The CLI loads your private capability internally; do not extract or print tokens. '
        'If your lease expired or was released, inspect status and claim this role only if free/reserved for you; '
        'never override another coordinator. A new claim creates the reporting capability. '
        'Do not invent progress or repeat placeholder text; if blocked, use --state blocked. '
        'If work finished, record --state done. Continue existing work after reporting.')
    binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    try:
        result=subprocess.run([binary,'--session',session,'agent','prompt',target['pane_id'],prompt],capture_output=True,text=True,timeout=8)
        confirmed=result.returncode==0
    except (OSError,subprocess.TimeoutExpired):confirmed=False
    with database(project) as db:event(db,leader,'progress-reminder',{'confirmed':confirmed,'session':session})
    return 'reminded' if confirmed else 'delivery_unconfirmed'
