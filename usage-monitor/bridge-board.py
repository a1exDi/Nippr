#!/usr/bin/env python3
"""Read-only live Bridge ledger board. No AI requests or state mutations."""
import argparse
import sys
import curses
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import shutil
import time
import textwrap
import unicodedata

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'universal-bridge'))
from project_store import state_dir

NAMES={'claude':'Claude','codex':'Codex','grok':'Grok','muse':'Muse','antigravity':'Antigravity'}


def read_state(project):
    try:path=state_dir(project)/'state.sqlite3'
    except ValueError as error:return {'error':str(error)}
    if not path.is_file():
        return {'error':'Bridge ещё не инициализирован в этом проекте.'}
    try:
        db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.3)
        db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN')
            lease=dict(db.execute('SELECT agent,expires,generation,target FROM lease WHERE id=1').fetchone())
            tasks=[dict(x) for x in db.execute('SELECT id,title,assignee,status,result,updated FROM tasks ORDER BY updated DESC')]
            workers={x['task']:dict(x) for x in db.execute('SELECT task,pane,session,report_at FROM workers')}
            checkpoints=[dict(x) for x in db.execute("SELECT at,agent,kind,body FROM events WHERE kind IN ('checkpoint','handoff','operator-switch','worker-report') ORDER BY id DESC LIMIT 20")]
            return {'readiness':[dict(r) for r in db.execute('SELECT * FROM readiness')], 'sessions':[dict(r)|{'panes':json.loads(r['panes'])} for r in db.execute('SELECT * FROM runtimes')], 'lease':lease,'tasks':tasks,'workers':workers,'events':checkpoints,'observed':time.time()}
        finally:db.close()
    except (sqlite3.Error,TypeError):
        return {'error':'Не удалось прочитать состояние; повторю обновление.'}


def process_states(project):
    import concurrent.futures
    runtimes=read_state(project).get('sessions',[])
    executable=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    def query(runtime):
        try:
            result=subprocess.run([executable,'--session',runtime['session'],'agent','list'],capture_output=True,text=True,timeout=2)
            if result.returncode:return []
            agents=json.loads(result.stdout)['result']['agents']
            return [(('antigravity' if a.get('agent')=='agy' else a.get('agent')),
                     {'status':str(a.get('agent_status','unknown'))+' @ '+runtime['session'],
                      'pane':a.get('pane_id')}) for a in agents
                     if a.get('pane_id') in runtime['panes'].values()]
        except (OSError,ValueError,KeyError,subprocess.TimeoutExpired):return []
    result={}
    if not runtimes:return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4,len(runtimes))) as pool:
        for items in pool.map(query,runtimes):
            for agent,info in items:
                if agent in result:
                    result[agent]['status']+='; '+info['status']
                else:result[agent]=info
    return result


def age(at):
    if not at:return 'нет отчётов'
    seconds=max(0,int(time.time()-at))
    return f'{seconds} с назад' if seconds<60 else f'{seconds//60} мин назад'


def coordinator_line(state):
    lease=state['lease']
    if lease['agent'] and (lease['expires'] or 0)>time.time():
        return f"👑 Координатор: {NAMES.get(lease['agent'],lease['agent'])} · осталось {int(lease['expires']-time.time())} с"
    if lease['target']:
        suffix='ожидает принятия' if (lease['expires'] or 0)>time.time() else 'срок принятия истёк'
        return f"Передача → {NAMES.get(lease['target'],lease['target'])}: {suffix}"
    if lease['agent']:return 'Нет активного координатора: срок управления истёк'
    return 'Координатор ещё не принял управление'


def cards(state,live):
    result=[]
    for agent,name in NAMES.items():
        tasks=[t for t in state['tasks'] if t['assignee']==agent and t['status'] not in ('done','cancelled')]
        role='координатор' if state['lease']['agent']==agent and (state['lease']['expires'] or 0)>time.time() else 'исполнитель'
        process=live.get(agent,{}).get('status','нет подключения к Herdr')
        result.append((f'{name} · {role} · процесс: {process}',True))
        for check in state.get('readiness',[]):
            if check['agent']==agent:
                fresh=' · устарело' if time.time()-check['checked']>300 else ''
                result.append(('Готовность: '+check['status']+fresh+' · '+age(check['checked'])+' · '+check['session'],False))
        if not tasks:result.append(('Назначенных активных задач нет',False))
        for task in tasks:
            worker=state['workers'].get(task['id']) or {}
            report_at=worker.get('report_at')
            stale=' · УСТАРЕЛ' if report_at and time.time()-report_at>300 else ''
            result.extend([(f"[{task['status']}] {task['title']} · {task['id']}",False),
                           ('Последний отчёт: '+age(report_at)+stale,False)])
            if worker.get('pane'):result.append(('Панель: '+worker['pane']+' · сессия: '+(worker.get('session') or '—'),False))
            if task['result']:result.append(('Прогресс: '+task['result'],False))
        result.append(('',False))
    return result


def board(screen,project):
    screen.keypad(True);screen.timeout(250)
    try:curses.curs_set(0)
    except curses.error:pass
    color=0
    if curses.has_colors():
        curses.start_color();curses.use_default_colors()
        curses.init_pair(1,curses.COLOR_CYAN,-1);color=curses.color_pair(1)
        curses.init_pair(2,-1,-1);screen.bkgd(' ',curses.color_pair(2))
    state={};live={};updated=0;process_updated=0;offset=0
    while True:
        now=time.monotonic()
        if now-updated>=1:
            state=read_state(project);updated=now
        if now-process_updated>=5:
            live=process_states(project);process_updated=now
        h,w=screen.getmaxyx();screen.erase()
        def put(y,x,text,attr=0,limit=None):
            if not (0<=y<h-1 and 0<=x<w-1):return
            budget=min(w-x-1,limit if limit is not None else w)
            out='';used=0
            for ch in str(text):
                size=0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in 'WF' else 1)
                if used+size>budget:break
                out+=ch;used+=size
            try:screen.addstr(y,x,out,attr)
            except curses.error:pass
        put(1,3,'🧭 BRIDGE — LIVE TEAM',color|curses.A_BOLD)
        put(2,3,str(project),curses.A_DIM)
        content=[]
        if 'error' in state:content=[(state['error'],False)]
        else:
            put(4,3,coordinator_line(state),curses.A_BOLD)
            put(5,3,'Задачи = отчёты Bridge; процесс Herdr = отдельный сигнал активности',curses.A_DIM)
            content=cards(state,live)
            content.append(('ПОСЛЕДНИЕ СОБЫТИЯ',True))
            for event in state['events'][:5]:
                body=json.loads(event['body'])
                summary=body.get('summary') or body.get('result') or body.get('reason') or ''
                content.append((f"{event['agent']} / {event['kind']} · {age(event['at'])}: {summary}",False))
        if h>=16 and w>=50:
            top=7;bottom=h-4
            put(top,2,'╭'+'─'*(w-6)+'╮',color)
            for y in range(top+1,bottom):put(y,2,'│',color);put(y,w-3,'│',color)
            put(bottom,2,'╰'+'─'*(w-6)+'╯',color)
            rows=[(part,bold) for line,bold in content for part in (textwrap.wrap(line,max(10,w-10)) or [''])]
            capacity=bottom-top-1;offset=min(offset,max(0,len(rows)-capacity))
            for i,(line,bold) in enumerate(rows[offset:offset+capacity]):put(top+1+i,4,line,curses.A_BOLD if bold else 0,w-8)
        else:put(7,2,'Разверни окно до 50 × 16 или больше.')
        put(h-2,3,'↑↓ / PgUp PgDn Прокрутка · R Обновить · Q Выход · данные раз в 1 с',color)
        screen.refresh();key=screen.getch()
        if key in (27,ord('q'),ord('Q')):return
        if key==curses.KEY_DOWN:offset+=1
        if key==curses.KEY_UP:offset=max(0,offset-1)
        if key==curses.KEY_NPAGE:offset+=10
        if key==curses.KEY_PPAGE:offset=max(0,offset-10)
        if key in (ord('r'),ord('R')):updated=0;process_updated=0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default=os.environ.get('BRIDGE_PROJECT',os.getcwd()))
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args();project=Path(args.project).expanduser().resolve()
    if args.json:print(json.dumps(read_state(project),ensure_ascii=False,indent=2))
    else:
        try:curses.wrapper(board,project)
        except KeyboardInterrupt:pass

if __name__=='__main__':main()
