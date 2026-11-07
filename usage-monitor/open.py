#!/usr/bin/env python3
import json
import shutil
import subprocess
import sys
herdr=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
if len(sys.argv)>1:
    session=sys.argv[1]
else:
    result=subprocess.run([herdr,'session','list','--json'],capture_output=True,text=True,check=True)
    sessions=[s['name'] for s in json.loads(result.stdout)['sessions'] if s.get('running')]
    if not sessions:
        sys.exit('Нет работающих сессий. Сначала запусти herdr-start или herdr.')
    if len(sessions)==1:
        session=sessions[0]
    else:
        for i,name in enumerate(sessions,1): print(f'{i}. {name}')
        try: session=sessions[int(input('Номер сессии: '))-1]
        except (ValueError,IndexError,EOFError): sys.exit('Некорректный выбор.')
subprocess.run([herdr,'--session',session,'plugin','pane','open','--plugin','local.usage-monitor',
                '--entrypoint','dashboard','--placement','tab','--focus'],check=True)
print('Панель открыта в сессии:',session)
