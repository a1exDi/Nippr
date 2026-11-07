#!/usr/bin/env python3
"""Install portable local commands; preserve unrelated existing files."""
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
root=Path(__file__).resolve().parent
bindir=Path.home()/'.local/bin'
bindir.mkdir(parents=True,exist_ok=True)
commands={'agent-bridge':root/'universal-bridge/bridge.py','herdr-start':root/'herdr-launcher.py',
          'bridge-board':root/'usage-monitor/bridge-board.py','herdr-usage':root/'usage-monitor/open.py'}
for name,script in commands.items():
    dest=bindir/name
    content='#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+' "$@"\n'
    if dest.exists() and dest.read_text()!=content:
        print('Preserved existing command:',dest)
        continue
    dest.write_text(content);dest.chmod(0o755);print('Installed:',dest)
herdr=shutil.which('herdr')
if herdr:subprocess.run([herdr,'plugin','link',str(root/'usage-monitor')],check=True)
else:print('Herdr not on PATH; plugin can be linked later with: herdr plugin link '+str(root/'usage-monitor'))
print('Add ~/.local/bin to PATH if needed. Claude statusline is optional and must be configured explicitly.')
