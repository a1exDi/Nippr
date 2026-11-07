"""Canonical per-project storage, shared by all Git worktrees."""
import fcntl
import os
from pathlib import Path
import subprocess


def git(project, *args):
    result=subprocess.run(['git','-C',str(project),*args],capture_output=True,text=True,timeout=5)
    return result.stdout.strip() if result.returncode==0 else None


def locations(project):
    root=Path(project).expanduser().resolve()
    if not root.is_dir():raise ValueError('Project directory does not exist')
    common=git(root,'rev-parse','--path-format=absolute','--git-common-dir')
    if not common:return root/'.agent-bridge', [root/'.agent-bridge']
    common=Path(common).resolve()
    canonical=common/'agent-bridge'
    worktrees=git(root,'worktree','list','--porcelain') or ''
    legacy=[Path(line[9:])/'.agent-bridge' for line in worktrees.splitlines() if line.startswith('worktree ')]
    legacy.append(root/'.agent-bridge')
    return canonical,list(dict.fromkeys(legacy))


def state_dir(project,create=False):
    canonical,legacy=locations(project)
    existing=[p for p in legacy if p!=canonical and (p/'state.sqlite3').exists()]
    if (canonical/'state.sqlite3').exists() and existing or len(existing)>1:
        raise ValueError('Multiple independent Bridge ledgers found for this repository; resolve them before continuing')
    if not create:
        return existing[0] if existing else canonical
    canonical.parent.mkdir(parents=True,exist_ok=True)
    lockpath=canonical.parent/('.bridge-migration.lock' if canonical.name=='agent-bridge' else '.agent-bridge-migration.lock')
    with lockpath.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        existing=[p for p in legacy if p!=canonical and (p/'state.sqlite3').exists()]
        if (canonical/'state.sqlite3').exists() and existing or len(existing)>1:
            raise ValueError('Conflicting ledgers; refusing automatic migration')
        if existing:
            if canonical.exists():raise ValueError('Destination already exists; migration requires review')
            # Renaming an active SQLite ledger could split writers. Require explicit migration offline.
            raise ValueError('Legacy Bridge ledger found at '+str(existing[0])+'. Stop Bridge writers and run migrate --offline')
        canonical.mkdir(mode=0o700,exist_ok=True)
    if canonical.is_symlink():raise ValueError('State directory must not be a symlink')
    return canonical


def migrate(project):
    canonical,legacy=locations(project)
    if canonical in legacy:return {'migrated':False,'state_dir':str(canonical)}
    canonical.parent.mkdir(parents=True,exist_ok=True)
    with (canonical.parent/'.bridge-migration.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        old=[p for p in legacy if p!=canonical and (p/'state.sqlite3').exists()]
        if not old:return {'migrated':False,'state_dir':str(canonical)}
        if len(old)!=1 or canonical.exists():raise ValueError('Conflicting ledgers; refusing migration')
        if old[0].is_symlink():raise ValueError('Symlink state directory refused')
        os.rename(old[0],canonical)
        return {'migrated':True,'state_dir':str(canonical),'previous':str(old[0])}
