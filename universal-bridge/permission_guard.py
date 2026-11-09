#!/usr/bin/env python3
"""User-authorized read-permission guard; bounded to a project and Herdr session."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import fcntl
from project_store import state_dir
from permissions import control
p=argparse.ArgumentParser();p.add_argument('--project',required=True);p.add_argument('--session',required=True)
a=p.parse_args();folder=state_dir(a.project)
# One guard per project/session; duplicate startup exits without affecting agents.
lock=folder/('permission-guard-'+__import__('hashlib').sha256(a.session.encode()).hexdigest()[:16]+'.lock')
with lock.open('a') as handle:
    os.chmod(lock,0o600)
    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit(0)
    failures=0
    while failures<3:
        try:
            binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
            check=subprocess.run([binary,'--session',a.session,'agent','list'],capture_output=True,text=True,timeout=5)
            if check.returncode:failures+=1;time.sleep(2);continue
            failures=0
            try:
                from coordinator_progress import nudge
                nudge(a.project,a.session,json.loads(check.stdout)['result']['agents'])
            except Exception:pass
            try:
                from presentation import sync
                sync(a.project)
            except Exception:pass
            with sqlite3.connect((folder/'state.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                row=db.execute('SELECT token,expires FROM lease WHERE id=1').fetchone()
            if row and row[0] and (row[1] or 0)>time.time():
                results=control(a.project,a.session,row[0])
                changed=[r for r in results if r['action'] in ('one_file_read_approved','scoped_command_approved')]
                if changed:print(json.dumps(changed),flush=True)
        except (OSError,ValueError,sqlite3.Error,subprocess.SubprocessError):pass
        time.sleep(2)
