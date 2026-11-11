"""Evidence-bound task acceptance. Commands are pinned by the coordinator, never workers."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import uuid
from project_store import git


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS task_evidence(task TEXT PRIMARY KEY, manifest TEXT NOT NULL, revision TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS task_checks(task TEXT PRIMARY KEY, plan TEXT NOT NULL, revision TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS task_verifications(id TEXT PRIMARY KEY, task TEXT NOT NULL, evidence_revision TEXT NOT NULL, plan_revision TEXT NOT NULL, status TEXT NOT NULL, receipt TEXT NOT NULL, checked REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS task_acceptances(task TEXT PRIMARY KEY, verification TEXT NOT NULL, agent TEXT NOT NULL, generation INTEGER NOT NULL, note TEXT NOT NULL, accepted REAL NOT NULL)')


def artifact(project, value):
    root = Path(project).resolve()
    path = Path(value)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ValueError('Artifact must be an explicit project-relative file')
    current = root
    for part in path.parts:
        if part in ('.git', '.agent-bridge', '.nippr-runtime') or part.startswith('.env'):
            raise ValueError('Private/runtime files cannot be artifacts')
        current = current / part
        if current.is_symlink():
            raise ValueError('Artifact symlinks are not allowed')
    if not current.is_file() or current.stat().st_size == 0:
        raise ValueError('Artifact must be an existing nonempty regular file')
    # Read once; a later transition independently rechecks the original file.
    content = current.read_bytes()
    return {'path': path.as_posix(), 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}


def invalidate(db, task):
    db.execute('DELETE FROM task_acceptances WHERE task=?', (task,))
    db.execute('DELETE FROM task_evidence WHERE task=?', (task,))


def submit(db, project, task, paths):
    if not paths:
        raise ValueError('Review requires --artifact FILE; a text report is not evidence')
    manifest = sorted([artifact(project, p) for p in sorted(set(paths))], key=lambda a: a['path'])
    worker = db.execute('SELECT resources FROM workers WHERE task=?', (task,)).fetchone()
    resources = json.loads(worker['resources']) if worker else []
    root = Path(project).resolve()
    checkout = Path(git(root, 'rev-parse', '--show-toplevel') or root).resolve()
    scoped_paths = [(root / a['path']).relative_to(checkout).as_posix() for a in manifest]
    if any(not any(p == r or p.startswith(r+'/') for r in resources) for p in scoped_paths):
        raise ValueError('Artifact must be inside the task reserved resources; reserve output paths too')
    invalidate(db, task)
    db.execute('INSERT INTO task_evidence VALUES(?,?,?)', (task, json.dumps(manifest), uuid.uuid4().hex))


def current(db, project, task):
    row = db.execute('SELECT * FROM task_evidence WHERE task=?', (task,)).fetchone()
    plan = db.execute('SELECT * FROM task_checks WHERE task=?', (task,)).fetchone()
    state = db.execute('SELECT status FROM tasks WHERE id=?', (task,)).fetchone()
    if not state or state['status'] != 'review' or not row or not plan:
        raise ValueError('Task must be in review with artifacts and a coordinator-pinned check')
    manifest = json.loads(row['manifest'])
    if [artifact(project, a['path']) for a in manifest] != manifest:
        raise ValueError('Artifact changed; worker must submit a new review')
    return row, plan


def verified(db, project, task, verification):
    evidence, plan = current(db, project, task)
    row = db.execute('SELECT * FROM task_verifications WHERE id=? AND task=?', (verification, task)).fetchone()
    if not row or row['status'] != 'passed' or row['evidence_revision'] != evidence['revision'] or row['plan_revision'] != plan['revision']:
        raise ValueError('A passing independent verification of the current evidence and check plan is required')
    latest = db.execute('SELECT id FROM task_verifications WHERE task=? ORDER BY checked DESC LIMIT 1', (task,)).fetchone()
    if not latest or latest['id'] != verification:
        raise ValueError('Only the latest verification can be accepted')
    receipt = json.loads(row['receipt'])
    log = Path(receipt['log'])
    if log.is_symlink() or not log.is_file() or hashlib.sha256(log.read_bytes()).hexdigest() != receipt['sha256']:
        raise ValueError('Verification log is missing or changed; run verification again')
    return row


def require_accepted(db, project, task, lease):
    row = db.execute('SELECT * FROM task_acceptances WHERE task=?', (task,)).fetchone()
    if not row or row['generation'] != lease['generation'] or row['agent'] != lease['agent']:
        raise ValueError('Current coordinator must explicitly task-accept before done')
    verified(db, project, task, row['verification'])


def configure(db, args):
    task = db.execute('SELECT status FROM tasks WHERE id=?', (args.id,)).fetchone()
    if not task or task['status'] in ('done', 'cancelled'):
        raise ValueError('Check plan requires an open task')
    argv = json.loads(args.command_json)
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x or '\0' in x for x in argv):
        raise ValueError('--command-json must be a nonempty JSON array of command arguments')
    if not 1 <= args.timeout <= 300:
        raise ValueError('Check timeout must be 1..300 seconds')
    plan = {'argv': argv, 'timeout': args.timeout}
    db.execute('INSERT OR REPLACE INTO task_checks VALUES(?,?,?)', (args.id, json.dumps(plan), uuid.uuid4().hex))
    db.execute('DELETE FROM task_acceptances WHERE task=?', (args.id,))
    return {'id': args.id, 'plan': plan}


def verify(args, bridge):
    token = args.token or os.environ.get('BRIDGE_TOKEN')
    with bridge.database(args.project) as db:
        lease = bridge.owner(db, token)
        bridge.require_reconciled(db, lease)
        evidence, plan = current(db, args.project, args.id)
        evidence, plan = dict(evidence), dict(plan)
        generation = lease['generation']
        identifier = uuid.uuid4().hex
        bridge.event(db, lease['agent'], 'verification-started', {'id': args.id, 'verification': identifier})
    # No SQLite write lock while the independent process runs.
    folder = bridge.state_dir(args.project) / 'verification-logs'
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('Verification log folder must not be a symlink')
    fd, name = tempfile.mkstemp(prefix=identifier+'-', suffix='.log', dir=str(folder))
    plan_data = json.loads(plan['plan'])
    status, exit_code = 'failed', None
    started = time.time()
    with os.fdopen(fd, 'wb') as output:
        try:
            process = subprocess.Popen(plan_data['argv'], cwd=str(Path(args.project).resolve()), stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                exit_code = process.wait(timeout=plan_data['timeout'])
                status = 'passed' if exit_code == 0 else 'failed'
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                status = 'timeout'
        except OSError as exc:
            output.write(('Check launch failed: '+str(exc)).encode())
    log = Path(name)
    receipt = {'log': str(log), 'sha256': hashlib.sha256(log.read_bytes()).hexdigest(), 'exit_code': exit_code, 'duration_seconds': round(time.time()-started, 3), 'plan': plan_data}
    with bridge.database(args.project) as db:
        lease = bridge.owner(db, token)
        bridge.require_reconciled(db, lease)
        try:
            now_evidence, now_plan = current(db, args.project, args.id)
            if generation != lease['generation'] or now_evidence['revision'] != evidence['revision'] or now_plan['revision'] != plan['revision']:
                status = 'stale'
        except ValueError:
            status = 'stale'
        db.execute('INSERT INTO task_verifications VALUES(?,?,?,?,?,?,?)', (identifier, args.id, evidence['revision'], plan['revision'], status, json.dumps(receipt), time.time()))
        db.execute('DELETE FROM task_acceptances WHERE task=?', (args.id,))
        bridge.event(db, lease['agent'], 'task-verified', {'id': args.id, 'verification': identifier, 'status': status, 'exit_code': exit_code})
    return {'id': args.id, 'verification': identifier, 'status': status, 'receipt': receipt}


def summary(db, task):
    evidence = db.execute('SELECT manifest,revision FROM task_evidence WHERE task=?', (task,)).fetchone()
    plan = db.execute('SELECT revision FROM task_checks WHERE task=?', (task,)).fetchone()
    verification = db.execute('SELECT id,status,evidence_revision,plan_revision,receipt FROM task_verifications WHERE task=? ORDER BY checked DESC LIMIT 1', (task,)).fetchone()
    accepted = db.execute('SELECT agent,generation,verification,accepted FROM task_acceptances WHERE task=?', (task,)).fetchone()
    check = None
    if verification:
        check = {'id': verification['id'], 'status': verification['status'],
                 'current_submission': bool(evidence and plan and evidence['revision'] == verification['evidence_revision'] and plan['revision'] == verification['plan_revision']),
                 'receipt': json.loads(verification['receipt'])}
    acceptance = dict(accepted) if accepted else None
    if acceptance:
        lease = db.execute('SELECT agent,generation FROM lease WHERE id=1').fetchone()
        acceptance['current_generation'] = bool(lease['agent'] == accepted['agent'] and lease['generation'] == accepted['generation'])
    return {'artifacts': json.loads(evidence['manifest']) if evidence else [], 'verification': check, 'acceptance': acceptance}
