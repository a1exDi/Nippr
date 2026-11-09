"""Confirm only known workspace trust dialogs for one explicitly authorized path."""
import json
from pathlib import Path
import shutil
import subprocess


def trust(project,session):
    project=Path(project).resolve();binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    base=[binary,'--session',session]
    def call(*args):return subprocess.run(base+list(args),capture_output=True,text=True,timeout=8)
    listed=call('agent','list')
    if listed.returncode:raise ValueError('Session unavailable')
    results=[]
    for a in json.loads(listed.stdout)['result']['agents']:
        pane=a['pane_id'];kind=a.get('agent');row={'agent':kind,'pane':pane,'action':'skipped'}
        cwd=a.get('foreground_cwd') or a.get('cwd')
        if not cwd or Path(cwd).resolve()!=project:
            row['reason']='Different project';results.append(row);continue
        screen=call('agent','read',pane,'--source','visible').stdout
        keys=None
        # Exact full path and recognized workspace-specific text are mandatory.
        if str(project) in screen:
            if kind=='muse' and 'Do you trust this workspace?' in screen and '1  Trust and continue' in screen:
                keys=['1','enter']
            elif kind=='claude' and 'Accessing workspace:' in screen and 'Yes, I trust this folder' in screen and 'read, edit, and' in screen:
                if '❯ No, exit' in screen:keys=['down','enter']
                elif '❯ Yes, I trust this folder' in screen:keys=['enter']
            elif kind=='agy' and 'Do you trust the contents of this project?' in screen and '> Yes, I trust this folder' in screen:
                keys=['enter']
            elif kind=='codex' and 'Yes, I trust this folder' in screen:
                row['reason']='Codex dialog not yet covered; no input sent'
        if keys:
            sent=call('agent','send-keys',pane,*keys)
            row['action']='confirmation_sent' if sent.returncode==0 else 'failed'
        else:row.setdefault('reason','No recognized trust dialog; no input sent')
        results.append(row)
    return results
