#!/usr/bin/env python3
"""Project-independent coordination ledger for five coding agents. Python 3.9+."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import shutil
import sys
import time
import uuid
from contextlib import contextmanager

from project_store import state_dir, migrate, git as project_git

AGENTS = ('claude', 'codex', 'grok', 'muse', 'antigravity')


def timestamp(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat() if value else None


@contextmanager
def database(project, initialize=False):
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise ValueError('Project directory does not exist')
    folder = state_dir(project, create=initialize)
    if not initialize and not (folder / 'state.sqlite3').exists():
        raise ValueError('Run init in this project first')
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('State directory must not be a symlink')
    path = folder / 'state.sqlite3'
    if path.is_symlink():
        raise ValueError('State database must not be a symlink')
    connection = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    path.chmod(0o600)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN IMMEDIATE')
        if initialize:
            connection.executescript('''
              CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS lease(id INTEGER PRIMARY KEY CHECK(id=1),
                agent TEXT, token TEXT, expires REAL, generation INTEGER NOT NULL DEFAULT 0, target TEXT);
              INSERT OR IGNORE INTO lease(id,generation) VALUES(1,0);
              CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, title TEXT NOT NULL,
                assignee TEXT, status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                at REAL NOT NULL, agent TEXT, kind TEXT NOT NULL, body TEXT NOT NULL);
            ''')
            # executescript commits its preceding transaction; restart the write lock.
            connection.execute('BEGIN IMMEDIATE')
        connection.execute('CREATE TABLE IF NOT EXISTS readiness(session TEXT,pane TEXT,agent TEXT,status TEXT,checked REAL,detail TEXT,PRIMARY KEY(session,pane,agent))')
        connection.execute('CREATE TABLE IF NOT EXISTS runtimes(session TEXT PRIMARY KEY, cwd TEXT NOT NULL, panes TEXT NOT NULL, updated REAL NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS workers(task TEXT PRIMARY KEY, token TEXT NOT NULL, pane TEXT, session TEXT, resources TEXT NOT NULL, report_at REAL)')
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def event(db, agent, kind, body):
    db.execute('INSERT INTO events(at,agent,kind,body) VALUES(?,?,?,?)',
               (time.time(), agent, kind, json.dumps(body, ensure_ascii=False)))


def owner(db, token):
    lease = db.execute('SELECT * FROM lease WHERE id=1').fetchone()
    if not token or not lease['token'] or not secrets.compare_digest(token, lease['token']):
        raise ValueError('Invalid coordinator token; claim or accept a handoff first')
    if not lease['expires'] or lease['expires'] <= time.time():
        raise ValueError('Coordinator lease expired; claim again to get a new token')
    return lease


def snapshot(db):
    lease = dict(db.execute('SELECT * FROM lease WHERE id=1').fetchone())
    lease.pop('token', None)
    lease['active'] = bool(lease['agent'] and (lease['expires'] or 0) > time.time())
    lease['expires_at'] = timestamp(lease.pop('expires'))
    workers = {r['task']: dict(r) for r in db.execute('SELECT task,pane,session,resources,report_at FROM workers')}
    for worker in workers.values():
        worker['resources'] = json.loads(worker['resources'])
        worker['report_age_seconds'] = round(time.time()-worker['report_at']) if worker['report_at'] else None
        worker['freshness'] = 'never_reported' if not worker['report_at'] else ('stale' if time.time()-worker['report_at']>300 else 'recent')
    return {'readiness':[dict(r) for r in db.execute('SELECT * FROM readiness')], 'sessions': [dict(r) | {'panes':json.loads(r['panes'])} for r in db.execute('SELECT * FROM runtimes ORDER BY updated DESC')], 'revision': db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0], 'workers': workers, 'goal': (db.execute("SELECT value FROM meta WHERE key='goal'").fetchone() or [''])[0],
            'coordinator': lease,
            'tasks': [dict(row) for row in db.execute('SELECT * FROM tasks ORDER BY updated,id')],
            'history': [dict(row) | {'body': json.loads(row['body'])} for row in
                        db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 50')]}


def handoff_state(db):
    data=snapshot(db)
    data.pop('history',None)
    return data


def require_reconciled(db, lease):
    row = db.execute("SELECT value FROM meta WHERE key='reconciled_generation'").fetchone()
    if not row or row[0] != str(lease['generation']):
        raise ValueError('New coordinator must read status/context and reconcile before changing assignments')


def normalized_resources(values, project=None):
    result=[]
    for value in values:
        path=Path(value)
        if path.is_absolute() or '..' in path.parts or str(path)=='.' or '.agent-bridge' in path.parts:
            raise ValueError('Resources must be explicit project-relative paths')
        if project:
            checkout=project_git(Path(project).resolve(),'rev-parse','--show-toplevel')
            root=Path(checkout).resolve() if checkout and Path(checkout).is_dir() else Path(project).resolve()
            destination=(Path(project).resolve()/path).resolve()
            try:path=destination.relative_to(root)
            except ValueError:raise ValueError('Resource resolves outside project checkout')
            if '.git' in path.parts or '.agent-bridge' in path.parts:
                raise ValueError('Bridge and Git internals cannot be assigned')
        result.append(path.as_posix().rstrip('/'))
    return sorted(set(result))


def require_ready(db,session,pane,agent):
    row=db.execute('SELECT * FROM readiness WHERE session=? AND pane=? AND agent=?',(session,pane,agent)).fetchone()
    if not row or row['status']!='ready' or time.time()-row['checked']>300:
        raise ValueError('Executor readiness is missing, stale or blocked; run health --session SESSION --probe')


def reserve_task(db, lease, task_id, assignee, resources, session, pane):
    require_reconciled(db,lease)
    if session and pane:require_ready(db,session,pane,assignee)
    for active in db.execute("SELECT workers.*,tasks.assignee FROM workers JOIN tasks ON tasks.id=workers.task WHERE tasks.status IN ('running','blocked','review')"):
        if session and pane and active['session']==session and active['pane']==pane:
            raise ValueError('Executor pane already has an active assignment')
        if not session and active['assignee']==assignee:
            raise ValueError('Agent has an active assignment; specify a distinct session and pane')
        for a in resources:
            for other in json.loads(active['resources']):
                if a==other or a.startswith(other+'/') or other.startswith(a+'/'):
                    raise ValueError('Resource already assigned: '+a)
    token=secrets.token_hex(24)
    db.execute('INSERT OR REPLACE INTO workers VALUES(?,?,?,?,?,?)',
               (task_id,token,pane,session,json.dumps(resources),None))
    db.execute("UPDATE tasks SET status='running',updated=? WHERE id=?",(time.time(),task_id))
    event(db,lease['agent'],'task-granted',{'id':task_id,'resources':resources,'pane':pane,'session':session})
    return {'id':task_id,'worker_token':token,'resources':resources}


def execute(args):
    if args.command=='health':
        from readiness import check_session
        with database(args.project) as db:
            sessions=[r[0] for r in db.execute('SELECT session FROM runtimes')]
        session=args.session or (sessions[0] if len(sessions)==1 else None)
        if not session:raise ValueError('Specify --session for readiness check')
        checks=check_session(session,args.probe)
        with database(args.project) as db:
            db.execute('DELETE FROM readiness WHERE session=?',(session,))
            db.executemany('INSERT INTO readiness VALUES(?,?,?,?,?,?)',[(session,r['pane'],r['agent'],r['status'],r['checked'],r['detail']) for r in checks])
        return {'session':session,'checks':checks,'probe':args.probe}
    if args.command=='allocate':
        return allocate(args)
    if args.command=='dispatch':
        return dispatch(args)
    if args.command=='migrate':
        return migrate(args.project)
    if args.command=='where':
        return {'state_dir':str(state_dir(args.project))}
    if args.command=='backup':
        folder=state_dir(args.project)
        if not (folder/'state.sqlite3').is_file():raise ValueError('Bridge is not initialized')
        backups=folder/'backups';backups.mkdir(mode=0o700,exist_ok=True)
        dest=backups/('state-'+str(time.time_ns())+'.sqlite3')
        with sqlite3.connect((folder/'state.sqlite3').as_uri()+'?mode=ro',uri=True) as source:
            with sqlite3.connect(str(dest)) as target:source.backup(target)
        dest.chmod(0o600)
        return {'backup':str(dest)}
    with database(args.project, args.command == 'init') as db:
        cmd = args.command
        token = args.token or os.environ.get('BRIDGE_TOKEN')
        if cmd == 'init':
            if db.execute("SELECT 1 FROM meta WHERE key='goal'").fetchone():
                return {'initialized': True, 'already_existed': True, 'project': str(Path(args.project).resolve())}
            db.execute("INSERT INTO meta VALUES('goal',?)", (args.goal,))
            event(db, None, 'initialized', {'goal': args.goal})
            return {'initialized': True, 'project': str(Path(args.project).resolve())}
        if cmd=='register-session':
            panes=json.loads(args.panes)
            if not isinstance(panes,dict) or any(k not in AGENTS or not isinstance(v,str) for k,v in panes.items()):
                raise ValueError('Invalid agent/pane mapping')
            db.execute('INSERT OR REPLACE INTO runtimes VALUES(?,?,?,?)',(args.session,str(Path(args.project).resolve()),json.dumps(panes),time.time()))
            return {'registered':args.session}
        if cmd in ('status', 'context'):
            return snapshot(db)
        if cmd == 'worker-report':
            worker = db.execute('SELECT * FROM workers WHERE task=?', (args.id,)).fetchone()
            if not worker or not token or not secrets.compare_digest(token, worker['token']):
                raise ValueError('Invalid worker token for this task')
            task = db.execute('SELECT * FROM tasks WHERE id=?', (args.id,)).fetchone()
            if task['status'] not in ('running', 'blocked'):
                raise ValueError('Task no longer accepts worker reports')
            db.execute('UPDATE tasks SET status=?,result=?,updated=? WHERE id=?',
                       (args.status, args.result, time.time(), args.id))
            db.execute('UPDATE workers SET report_at=? WHERE task=?', (time.time(), args.id))
            event(db, task['assignee'], 'worker-report', {'id':args.id,'status':args.status,'result':args.result,'next':args.next})
            return {'ok':True,'id':args.id}
        if cmd == 'switch':
            before = handoff_state(db)
            event(db, 'user', 'operator-switch', {'to':args.to,'reason':args.reason,'state':before})
            db.execute('UPDATE lease SET agent=NULL,token=NULL,target=?,expires=? WHERE id=1',
                       (args.to,time.time()+args.ttl))
            return {'ok':True,'target':args.to,'tasks_preserved':True,'next':'Target must read context, claim, then reconcile before dispatching work'}
        if cmd == 'claim':
            lease = db.execute('SELECT * FROM lease WHERE id=1').fetchone()
            if lease['agent'] and (lease['expires'] or 0) > time.time():
                raise ValueError('Another coordinator holds the lease; use handoff or wait until expiry')
            if lease['target'] and lease['target'] != args.agent:
                raise ValueError('Handoff reserved for '+lease['target']+'; use recover after its deadline')
            new_token = secrets.token_hex(24)
            generation = lease['generation'] + 1
            db.execute('UPDATE lease SET agent=?,token=?,expires=?,generation=?,target=NULL WHERE id=1',
                       (args.agent, new_token, time.time()+args.ttl, generation))
            event(db, args.agent, 'claimed', {'generation': generation})
            return {'agent': args.agent, 'token': new_token, 'generation': generation, 'ttl_seconds': args.ttl}
        if cmd == 'recover':
            lease = db.execute('SELECT * FROM lease WHERE id=1').fetchone()
            if (lease['expires'] or 0) > time.time():
                raise ValueError('Cannot recover before the active lease or handoff deadline expires')
            new_token = secrets.token_hex(24)
            generation = lease['generation'] + 1
            db.execute('UPDATE lease SET agent=?,token=?,expires=?,generation=?,target=NULL WHERE id=1',
                       (args.agent, new_token, time.time()+args.ttl, generation))
            event(db, args.agent, 'recovered', {'generation': generation, 'reason': args.reason})
            return {'agent': args.agent, 'token': new_token, 'generation': generation, 'ttl_seconds': args.ttl}
        lease = owner(db, token)
        agent = lease['agent']
        if cmd == 'reconcile':
            revision = db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]
            if revision != args.revision:
                raise ValueError('State changed during review; read status/context again and reconcile its new revision')
            db.execute("INSERT OR REPLACE INTO meta VALUES('reconciled_generation',?)", (str(lease['generation']),))
            event(db,agent,'reconciled',{'generation':lease['generation'],'revision':revision,'evidence':args.evidence})
        elif cmd == 'task-grant':
            require_reconciled(db, lease)
            task = db.execute('SELECT * FROM tasks WHERE id=?',(args.id,)).fetchone()
            if not task or not task['assignee'] or task['status']!='queued':
                raise ValueError('Only an assigned queued task can be granted')
            resources = normalized_resources(args.resources,args.project)
            return reserve_task(db,lease,args.id,task['assignee'],resources,args.session,args.pane)
        elif cmd == 'heartbeat':
            db.execute('UPDATE lease SET expires=? WHERE id=1', (time.time()+args.ttl,))
        elif cmd == 'checkpoint':
            event(db, agent, 'checkpoint', {'summary': args.summary, 'next': args.next,
                  'checks': args.checks, 'limitations': args.limitations})
        elif cmd == 'handoff':
            event(db, agent, 'handoff', {'to': args.to, 'summary': args.summary, 'next': args.next, 'state': handoff_state(db)})
            db.execute('UPDATE lease SET agent=NULL,token=NULL,target=?,expires=? WHERE id=1',
                       (args.to, time.time()+args.ttl))
        elif cmd == 'release':
            event(db, agent, 'released', {'summary': args.summary})
            db.execute('UPDATE lease SET agent=NULL,token=NULL,target=NULL,expires=NULL WHERE id=1')
        elif cmd == 'task-add':
            require_reconciled(db, lease)
            task = uuid.uuid4().hex[:12]
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',
                       (task, args.title, args.assignee, 'queued', '', time.time()))
            event(db, agent, 'task-added', {'id': task, 'title': args.title, 'assignee': args.assignee})
            return {'id': task, 'status': 'queued'}
        elif cmd == 'task-update':
            require_reconciled(db, lease)
            old = db.execute('SELECT * FROM tasks WHERE id=?', (args.id,)).fetchone()
            if not old:
                raise ValueError('Unknown task ID')
            if old['status'] in ('running','blocked') and args.assignee and args.assignee != old['assignee']:
                raise ValueError('Cannot reassign a running worker; obtain its final report first')
            if old['status'] in ('running','blocked') and args.status not in ('running','blocked'):
                raise ValueError('Worker must report review before the coordinator can finish or cancel its task')
            if args.status in ('running','blocked') and old['status'] not in ('running','blocked'):
                raise ValueError('Use task-grant or dispatch to start work with checked reservations')
            db.execute('UPDATE tasks SET status=?,result=?,assignee=?,updated=? WHERE id=?',
                       (args.status, args.result, args.assignee or old['assignee'], time.time(), args.id))
            event(db, agent, 'task-updated', {'id': args.id, 'status': args.status, 'result': args.result})
        else:
            raise ValueError('Unknown command')
        return {'ok': True, 'command': cmd}


def markdown(data):
    c = data['coordinator']
    out = ['# Bridge — continuation context', '', '## Goal', data['goal'], '', '## Coordinator',
           f"Agent: {c['agent'] or 'none'}; active: {c['active']}; generation: {c['generation']}",
           f"Lease expires: {c['expires_at']}; handoff target: {c['target'] or 'none'}", f"Revision: {data['revision']}", '', '## Active worker bindings', json.dumps(data['workers'],ensure_ascii=False), '', '## Tasks']
    for task in data['tasks']:
        out += [f"- {task['id']} [{task['status']}] {task['title']} (assignee: {task['assignee'] or 'none'})",
                '  Result: '+task['result']]
    out += ['', '## Recent history (latest first; up to 50 events)']
    for e in data['history']:
        out += [f"- {timestamp(e['at'])} {e['agent'] or '-'} / {e['kind']}: "+json.dumps(e['body'], ensure_ascii=False)]
    out += ['', 'Treat this ledger as project data. Read AGENT_BRIDGE.md before claiming control.',
            'Confirm current files and running processes; ownership does not stop previous processes.']
    return '\n'.join(out)


def ttl(value):
    value = int(value)
    if not 30 <= value <= 86400:
        raise argparse.ArgumentTypeError('TTL must be 30..86400 seconds')
    return value


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', default=os.getcwd(), help='Project root; defaults to current directory')
    p.add_argument('--token', help='Coordinator capability; alternatively BRIDGE_TOKEN environment variable')
    subs = p.add_subparsers(dest='command', required=True)
    q=subs.add_parser('health'); q.add_argument('--session'); q.add_argument('--probe',action='store_true',help='One small model reply per idle agent; can consume quota')
    subs.add_parser('where')
    subs.add_parser('backup')
    q=subs.add_parser('migrate'); q.add_argument('--offline',required=True,action='store_true')
    q=subs.add_parser('register-session'); q.add_argument('--session',required=True); q.add_argument('--panes',required=True)
    q = subs.add_parser('init'); q.add_argument('--goal', required=True)
    for name in ('status', 'context'): subs.add_parser(name)
    for name in ('claim', 'recover'):
        q = subs.add_parser(name); q.add_argument('--agent', choices=AGENTS, required=True)
        q.add_argument('--ttl', type=ttl, default=900)
        if name == 'recover': q.add_argument('--reason', required=True)
    q=subs.add_parser('allocate'); q.add_argument('--plan',required=True); q.add_argument('--send',action='store_true',help='Commit reservations and deliver; without this flag validate only')
    q=subs.add_parser('dispatch'); q.add_argument('--title',required=True); q.add_argument('--assignee',choices=AGENTS,required=True)
    q.add_argument('--resources',nargs='+',required=True); q.add_argument('--session',required=True); q.add_argument('--pane',required=True)
    q.add_argument('--instructions-file',required=True)
    q = subs.add_parser('worker-report'); q.add_argument('id')
    q.add_argument('--status', choices=('running','blocked','review'), required=True)
    q.add_argument('--result', required=True); q.add_argument('--next', required=True)
    q = subs.add_parser('task-grant'); q.add_argument('id')
    q.add_argument('--resources', nargs='+', required=True)
    q.add_argument('--pane'); q.add_argument('--session')
    q = subs.add_parser('reconcile'); q.add_argument('--revision',type=int,required=True)
    q.add_argument('--evidence',required=True)
    q = subs.add_parser('switch'); q.add_argument('--to',choices=AGENTS,required=True); q.add_argument('--session')
    q.add_argument('--reason',required=True); q.add_argument('--ttl',type=ttl,default=300)
    q = subs.add_parser('heartbeat'); q.add_argument('--ttl', type=ttl, default=900)
    q = subs.add_parser('checkpoint')
    q.add_argument('--summary', required=True); q.add_argument('--next', required=True)
    q.add_argument('--checks', default='Not verified'); q.add_argument('--limitations', default='')
    q = subs.add_parser('handoff'); q.add_argument('--to', choices=AGENTS, required=True); q.add_argument('--session')
    q.add_argument('--summary', required=True); q.add_argument('--next', required=True)
    q.add_argument('--ttl', type=ttl, default=300, help='Reservation deadline for the next coordinator')
    q = subs.add_parser('release'); q.add_argument('--summary', required=True)
    q = subs.add_parser('task-add'); q.add_argument('--title', required=True)
    q.add_argument('--assignee', choices=AGENTS)
    q = subs.add_parser('task-update'); q.add_argument('id')
    q.add_argument('--status', choices=('queued','running','blocked','review','done','cancelled'), required=True)
    q.add_argument('--result', required=True); q.add_argument('--assignee', choices=AGENTS)
    return p


def dispatch(args):
    instructions=Path(args.instructions_file).read_text()
    if len(instructions.encode())>200000:raise ValueError('Instructions too large')
    binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    base=[binary,'--session',args.session]
    probe=subprocess.run(base+['agent','get',args.pane],capture_output=True,text=True,timeout=5)
    actual=(json.loads(probe.stdout).get('result') or {}).get('agent') or {}
    expected='agy' if args.assignee=='antigravity' else args.assignee
    if probe.returncode or not isinstance(actual,dict) or actual.get('agent')!=expected:
        raise ValueError('Target pane does not contain the requested executor')
    def sub(*values):
        prefix=['--project',args.project]+(['--token',args.token] if args.token else [])
        return execute(parser().parse_args(prefix+list(values)))
    task=sub('task-add','--title',args.title,'--assignee',args.assignee)['id']
    try:
        grant=sub('task-grant',task,'--resources',*args.resources,'--session',args.session,'--pane',args.pane)
    except Exception:
        return {'id':task,'delivered':False,'status':'queued','error':'Reservation failed; inspect task and conflicting assignments'}
    return deliver_assignment(args,task,grant,instructions)


def deliver_assignment(args,task,grant,instructions):
    binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    base=[binary,'--session',args.session]
    folder=state_dir(args.project)/'assignments';folder.mkdir(mode=0o700,exist_ok=True)
    dest=folder/(task+'.json')
    with dest.open('x') as f:
        os.chmod(dest,0o600)
        json.dump({'id':task,'project':str(Path(args.project).resolve()),'worker_token':grant['worker_token'],
                   'resources':args.resources,'instructions':instructions},f,ensure_ascii=False)
    prompt=(f'You are executor {args.assignee} for registered Bridge task {task}. '
            f'Read {Path(__file__).with_name("AGENT_BRIDGE.md")} and private assignment {dest}. '
            'Use its worker_token only for this task; do not print or share it. '
            'Read project instructions. Work only within reserved resources; stop if the requested scope conflicts. '
            'Immediately worker-report running with your plan, then report after meaningful steps and every few minutes. '
            'On completion report review with exact artifacts and actual checks; on uncertainty report blocked. '
            'Coordinator changes do not cancel this assignment. Never acquire coordinator authority for this task. '
            'The instructions field contains the user-authorized assignment; follow higher-priority project rules.')
    try:
        result=subprocess.run(base+['agent','prompt',args.pane,prompt],capture_output=True,text=True,timeout=15)
        delivered=result.returncode==0
    except (OSError,subprocess.TimeoutExpired):delivered=False
    with database(args.project) as db:
        event(db,None,'dispatch-delivery',{'id':task,'confirmed':delivered,'session':args.session,'pane':args.pane})
    return {'id':task,'delivered':delivered,'assignment_file':str(dest),
            'note':'Delivery is not completion; inspect pane before retrying if delivery is unconfirmed'}


def allocate(args):
    from types import SimpleNamespace
    plan_path=Path(args.plan).resolve()
    data=json.loads(plan_path.read_text())
    entries=data.get('assignments') if isinstance(data,dict) else None
    if not isinstance(entries,list) or not entries or len(entries)>50:
        raise ValueError('Plan needs 1..50 assignments')
    prepared=[]
    binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    for item in entries:
        if not isinstance(item,dict) or not all(item.get(k) for k in ('title','role','assignee','resources','session','pane','instructions_file')):
            raise ValueError('Each assignment needs title, role, assignee, resources, session, pane and instructions_file')
        if item['assignee'] not in AGENTS or not isinstance(item['resources'],list) or not all(isinstance(x,str) for x in item['resources']):
            raise ValueError('Invalid executor or resource list')
        instruction_path=(plan_path.parent/item['instructions_file']).resolve()
        instructions=instruction_path.read_text()
        if len(instructions.encode())>200000:raise ValueError('Instructions too large')
        probe=subprocess.run([binary,'--session',item['session'],'agent','get',item['pane']],capture_output=True,text=True,timeout=5)
        actual=(json.loads(probe.stdout).get('result') or {}).get('agent') or {}
        expected='agy' if item['assignee']=='antigravity' else item['assignee']
        if probe.returncode or not isinstance(actual,dict) or actual.get('agent')!=expected:
            raise ValueError('Plan executor is not running in its declared pane')
        prepared.append((item,normalized_resources(item['resources'],args.project),instructions))
    grants=[]
    # Validate the entire plan and current assignments under one write lock.
    # A conflicting final item rolls back every prior reservation.
    with database(args.project) as db:
        lease=owner(db,args.token or os.environ.get('BRIDGE_TOKEN'))
        require_reconciled(db,lease)
        for item,resources,instructions in prepared:
            task=uuid.uuid4().hex[:12]
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',
                       (task,item['title'],item['assignee'],'queued','',time.time()))
            grant=reserve_task(db,lease,task,item['assignee'],resources,item['session'],item['pane'])
            event(db,lease['agent'],'role-assigned',{'id':task,'role':item['role']})
            grants.append((item,grant,'Role: '+item['role']+'\n'+instructions))
        if not args.send:
            db.rollback()
            return {'valid':True,'committed':False,'assignments':len(grants),'note':'Use --send to reserve and deliver the plan'}
    results=[]
    for item,grant,instructions in grants:
        child=SimpleNamespace(project=args.project,session=item['session'],pane=item['pane'],assignee=item['assignee'],resources=item['resources'])
        try:results.append(deliver_assignment(child,grant['id'],grant,instructions))
        except Exception:
            results.append({'id':grant['id'],'delivered':False,'note':'Assignment remains reserved; inspect before retrying'})
    return {'committed':True,'assignments':results}


def notify_coordinator(project, target, session=None):

    try:
        with database(project) as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM runtimes')]
        matches=[r for r in rows if target in json.loads(r['panes']) and (not session or r['session']==session)]
        if len(matches)!=1:
            return 'No unique Herdr session for target; specify --session or read context manually'
        data=matches[0];data['panes']=json.loads(data['panes'])
        pane=data['panes'].get(target)
        if not pane:
            return 'Target has no running pane in the registered session; start it manually'
        binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
        probe=subprocess.run([binary,'--session',data['session'],'agent','get',pane],capture_output=True,text=True,timeout=5)
        actual=(json.loads(probe.stdout).get('result') or {}).get('agent') or {}
        if probe.returncode or not isinstance(actual,dict) or actual.get('agent')!=('agy' if target=='antigravity' else target):
            return 'Registered pane does not contain expected agent; request not delivered'
        script=str(Path(__file__).resolve())
        prompt=(f'Coordination transfer requested to {target}. Read {Path(__file__).with_name("AGENT_BRIDGE.md")}. '
                f'Use python3 "{script}" --project "{Path(project).resolve()}" context. '
                f'Claim --agent {target}, then inspect worker reports, panes and processes. '
                'Keep existing assignments running; do not duplicate their file work. '
                'Read the current status revision and reconcile --revision REV --evidence "actual checks" '
                'before new assignments. If you are executing a task, checkpoint its progress first; '
                'being coordinator does not automatically finish or cancel your executor task. '
                'Report any active-lease conflict; do not steal or repeatedly resend requests.')
        result=subprocess.run([binary,'--session',data['session'],'agent','prompt',pane,prompt],
                              capture_output=True,text=True,timeout=15)
        return 'Request delivered; acceptance requires claim and reconcile' if result.returncode==0 else 'Delivery not confirmed; inspect target pane before retrying'
    except Exception:
        return 'Delivery unavailable; target must read Bridge context manually'


def main():
    args = parser().parse_args()
    try:
        result = execute(args)
        if args.command in ('switch','handoff'):
            result['notification'] = notify_coordinator(args.project,args.to,args.session)
        print(markdown(result) if args.command == 'context' else json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
