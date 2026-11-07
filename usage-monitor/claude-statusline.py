#!/usr/bin/env python3
"""Capture only documented usage fields; never stores prompts or credentials."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
try:
    data=json.load(sys.stdin)
    safe={key:data[key] for key in ('session_id','model','workspace','context_window','cost','rate_limits') if key in data}
    safe['_observed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
    folder=Path.home()/'.local/state/herdr-usage'
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    session=re.sub(r'[^a-zA-Z0-9_-]','_',str(data.get('session_id','unknown')))[:100]
    dest=folder/('claude-'+session+'.json')
    temp=folder/(dest.name+'.'+str(os.getpid())+'.tmp')
    with temp.open('w') as f:
        os.chmod(temp,0o600)
        json.dump(safe,f)
    os.replace(temp,dest)
    ctx=data.get('context_window') or {}
    pct=ctx.get('used_percentage')
    model=(data.get('model') or {}).get('display_name','Claude')
    print(f'{model} · контекст {pct}% · 📊 Herdr Usage' if pct is not None else f'{model} · 📊 Herdr Usage')
except (ValueError,OSError,TypeError):
    print('Claude · Usage Monitor: нет данных')
