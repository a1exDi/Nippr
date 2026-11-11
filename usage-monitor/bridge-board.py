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
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'task_evidence','task_checks','task_verifications','task_acceptances'}.issubset(tables):
                from acceptance import summary
                for task in tasks:task['evidence']=summary(db,task['id'])
            workers={x['task']:dict(x) for x in db.execute('SELECT task,pane,session,report_at FROM workers')}
            checkpoints=[dict(x) for x in db.execute("SELECT at,agent,kind,body FROM events WHERE kind IN ('checkpoint','handoff','operator-switch','worker-report') ORDER BY id DESC LIMIT 20")]
            reports={}
            for row in db.execute("SELECT body FROM events WHERE kind='worker-report' ORDER BY id DESC LIMIT 500"):
                payload=json.loads(row['body'])
                if payload.get('id') and payload['id'] not in reports:reports[payload['id']]=payload
            coordinator_updates={}
            for row in db.execute("SELECT agent,at,body FROM events WHERE kind='checkpoint' ORDER BY id DESC"):
                if row['agent'] not in coordinator_updates:coordinator_updates[row['agent']]={'at':row['at'],**json.loads(row['body'])}
            from bridge import designated_coordinator
            return {'coordinator_progress':{r['agent']:dict(r) for r in db.execute('SELECT * FROM coordinator_progress')},'coordinator_updates':coordinator_updates,'designated_coordinator':designated_coordinator(db),'reports':reports,'readiness':[dict(r) for r in db.execute('SELECT * FROM readiness')], 'sessions':[dict(r)|{'panes':json.loads(r['panes'])} for r in db.execute('SELECT * FROM runtimes')], 'lease':lease,'tasks':tasks,'workers':workers,'events':checkpoints,'observed':time.time()}
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
    designated=state.get('designated_coordinator')
    if designated:return '👑 Координатор: '+NAMES.get(designated,designated)+' · роль назначена, право управления Bridge сейчас не активно'
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


def agent_view(state, live, agent):
    tasks=[t for t in state['tasks'] if t['assignee']==agent]
    active=sorted([t for t in tasks if t['status'] not in ('done','cancelled')],key=lambda t:({'blocked':0,'running':1,'review':2,'queued':3}.get(t['status'],4),-t['updated']))
    completed=[t for t in tasks if t['status']=='done']
    cancelled=[t for t in tasks if t['status']=='cancelled']
    leader=state.get('designated_coordinator')==agent or (state['lease']['agent']==agent and (state['lease']['expires'] or 0)>time.time())
    status_names={'running':'Выполняет задачу','queued':'Ожидает запуска','blocked':'Нужна помощь',
                  'review':'Результат ждёт проверки','done':'Завершено','cancelled':'Отменено'}
    current=active[0] if active else None
    recent=completed[0] if completed else None
    process=(live.get(agent,{}).get('status','').split(' @ ')[0])
    process_label={'working':'работает','idle':'ожидает','done':'ответ готов','blocked':'запрос разрешения',
                   'unknown':'не определён'}.get(process,'нет связи')
    status=status_names.get(current['status'],'Без активного задания') if current else 'Без активного задания'
    if process=='working' and not current:status='Координатор работает' if leader else 'Работает в терминале без задачи Bridge'
    if process=='blocked':status='Ожидает разрешения'
    checkpoint=(state.get('coordinator_updates') or {}).get(agent,{}) if leader else {}
    activity=('Координирует проект — процесс активен' if leader else 'Выполняет работу в терминале') if process=='working' and not current else ('Ожидает запроса пользователя' if leader and not current else 'Задание не назначено')
    worker=state['workers'].get(current['id'],{}) if current else {}
    report=(state.get('reports') or {}).get(current['id'],{}) if current else {}
    progress=(state.get('coordinator_progress') or {}).get(agent,{}) if leader else {}
    if progress and progress['generation']!=state['lease']['generation']:progress={}
    progress_stale=bool(progress and time.time()-progress['updated']>90)
    if progress and not progress_stale and progress['status'] in ('working','blocked'):
        activity=progress['current_task']
        if progress['status']=='blocked':status='Координатору нужна помощь'
    return {'progress':progress,'progress_stale':progress_stale,'agent':agent,'name':NAMES[agent],'leader':leader,'status':status,'process':process_label,
            'activity':activity,'checkpoint':checkpoint,'active':active,'completed':completed,'cancelled':cancelled,'current':current,'recent':recent,
            'report_at':worker.get('report_at'),'updated_at':(current or recent or {}).get('updated'),'next':report.get('next'),
            'summary':current.get('result') if current else (recent.get('result') if recent else ''),
            'readiness':sorted([r for r in state.get('readiness',[]) if r['agent']==agent],key=lambda r:r['checked'],reverse=True)}


def panel_switch(project,target,session,pane,generation,previous):
    from bridge import execute,parser,notify_coordinator
    binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    probe=subprocess.run([binary,'--session',session,'agent','get',pane],capture_output=True,text=True,timeout=5)
    try:agent=json.loads(probe.stdout)['result']['agent']
    except (ValueError,KeyError):raise ValueError('Не удалось проверить выбранного агента')
    expected='agy' if target=='antigravity' else target
    if probe.returncode or agent.get('agent')!=expected:raise ValueError('Агент больше не работает в выбранной панели')
    cwd=agent.get('foreground_cwd') or agent.get('cwd')
    if not cwd or state_dir(cwd)!=state_dir(project):raise ValueError('Агент находится в другом проекте')
    if agent.get('agent_status')=='blocked' or not agent.get('interactive_ready'):
        raise ValueError('Агент ожидает разрешения или ещё не готов; сначала разблокируй его')
    result=execute(parser().parse_args(['--project',str(project),'switch','--to',target,
        '--session',session,'--reason','User switched coordinator in Live Team panel',
        '--expected-generation',str(generation),'--expected-coordinator',previous or 'none']))
    from presentation import sync
    try:sync(project)
    except Exception:pass
    result['notification']=notify_coordinator(project,target,session)
    return result


def switch_dialog(screen,project,state,target):
    candidates=[(r['session'],r['panes'][target]) for r in state.get('sessions',[]) if target in r['panes']]
    if not candidates:return None,'У агента нет зарегистрированной панели Herdr'
    current=0
    while True:
        h,w=screen.getmaxyx();width=min(86,w-4);height=min(h-4,12+len(candidates));y=max(1,(h-height)//2);x=max(1,(w-width)//2)
        def put(row,text,bold=False):
            try:screen.addnstr(y+row,x+2,text+' '*width,width-4,curses.A_BOLD if bold else 0)
            except curses.error:pass
        try:
            screen.addstr(y,x,'╭'+'─'*(width-2)+'╮')
            for yy in range(1,height-1):screen.addstr(y+yy,x,'│'+' '*(width-2)+'│')
            screen.addstr(y+height-1,x,'╰'+'─'*(width-2)+'╯')
        except curses.error:pass
        previous=state.get('designated_coordinator') or state['lease'].get('agent')
        put(1,'👑 Смена координатора',True)
        put(3,NAMES.get(previous,'Не назначен')+'  →  '+NAMES[target],True)
        put(4,'Задачи исполнителей сохранятся. Старое право управления будет отозвано.')
        put(5,'Новый ведущий должен принять роль и сверить состояние проекта.')
        for i,(session,pane) in enumerate(candidates):put(7+i,('› ' if i==current else '  ')+session+' · '+pane)
        put(height-3,'↑↓ Сессия   ·   Enter Передать управление   ·   Esc Отмена',True)
        screen.refresh();key=screen.getch()
        if key==27:return None,'Переключение отменено'
        if key==curses.KEY_UP:current=max(0,current-1)
        if key==curses.KEY_DOWN:current=min(len(candidates)-1,current+1)
        if key in (10,13,curses.KEY_ENTER):
            session,pane=candidates[current]
            return (project,target,session,pane,state['lease']['generation'],previous),None


def board(screen,project):
    screen.keypad(True);screen.timeout(250)
    try:curses.mousemask(curses.ALL_MOUSE_EVENTS)
    except curses.error:pass
    try:curses.curs_set(0)
    except curses.error:pass
    names=list(NAMES)
    palette={name:0 for name in names};accent=0
    if curses.has_colors():
        curses.start_color();curses.use_default_colors()
        colors=[213,81,215,114,111] if curses.COLORS>=256 else [curses.COLOR_MAGENTA,curses.COLOR_CYAN,curses.COLOR_YELLOW,curses.COLOR_GREEN,curses.COLOR_BLUE]
        for i,(name,fg) in enumerate(zip(names,colors),1):
            curses.init_pair(i,fg,-1);palette[name]=curses.color_pair(i)
        curses.init_pair(6,-1,-1);screen.bkgd(' ',curses.color_pair(6));accent=palette['codex']
    state={'error':'Загружаю состояние…'};live={}
    updated=process_updated=badge_updated=float('-inf')
    import concurrent.futures
    switch_pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    switching=None;notice=''
    offset=0;selected=0;details=False
    while True:
        now=time.monotonic()
        if switching is not None and switching.done():
            try:
                result=switching.result()
                notice='Роль передана; ожидается принятие новым координатором'
                if 'Request delivered' not in result.get('notification',''):notice='Роль переключена, уведомление не подтверждено — проверь агента'
            except Exception as error:notice='Не переключено: '+str(error)
            switching=None;updated=process_updated=badge_updated=float('-inf')
        if now-updated>=1:
            state=read_state(project);updated=now
            leader=state.get('designated_coordinator') or state.get('lease',{}).get('agent')
            chosen=names[selected]
            names=sorted(NAMES,key=lambda name:0 if name==leader else 1)
            selected=names.index(chosen)
        if now-process_updated>=5:live=process_states(project);process_updated=now
        if now-badge_updated>=5:
            try:
                from presentation import sync
                sync(project)
            except Exception:pass
            badge_updated=now
        h,w=screen.getmaxyx();screen.erase()
        hits=[];button_x=max(3,w-37)
        def put(y,x,text,attr=0,limit=None):
            if not (0<=y<h-1 and 0<=x<w-1):return
            budget=min(w-x-1,limit if limit is not None else w);out='';used=0
            for ch in str(text):
                size=0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in 'WF' else 1)
                if used+size>budget:break
                out+=ch;used+=size
            try:screen.addstr(y,x,out,attr)
            except curses.error:pass
        put(1,3,'N I P P R   /   КОМАНДА ПРОЕКТА',accent|curses.A_BOLD)
        put(2,3,Path(project).name+'  ·  '+time.strftime('%H:%M:%S'),curses.A_DIM)
        if 'error' in state:
            put(5,3,state['error'])
        elif h<23 or w<64:
            put(5,3,'Разверни терминал хотя бы до 64 × 23.')
        else:
            put(4,3,coordinator_line(state),accent|curses.A_BOLD)
            counts={s:sum(t['status']==s for t in state['tasks']) for s in ('running','blocked','review','done')}
            put(5,3,f"В работе {counts['running']}   ·   Нужна помощь {counts['blocked']}   ·   На проверке {counts['review']}   ·   Завершено {counts['done']}")
            top=7;bottom=h-4;area=bottom-top
            def vp(y,x,text,attr=0,limit=None):
                if top<=y<bottom:put(y,x,text,attr,limit)
            def frame(y,x,height,width,title,col):
                vp(y,x,'╭'+'─'*(width-2)+'╮',col)
                for yy in range(y+1,y+height-1):vp(yy,x,'│',col);vp(yy,x+width-1,'│',col)
                vp(y+height-1,x,'╰'+'─'*(width-2)+'╯',col)
                vp(y,x+2,' '+title+' ',col|curses.A_BOLD,width-5)
            views=[agent_view(state,live,a) for a in names]
            if details:
                view=views[selected];col=palette[view['agent']];width=w-4
                rows=[('СЕЙЧАС: '+view['status'],True),('Процесс: '+view['process'],False),('',False)]
                if view['leader']:
                    rows.append(('Работа координатора: '+view['activity'],True))
                    progress=view['progress']
                    if progress:
                        rows.extend([('Текущая задача: '+progress['current_task'],True),('Сделано: '+progress['completed'],False),('Следующий шаг: '+progress['next_step'],False),('Проверки: '+progress['checks'],False),('Обновлено: '+age(progress['updated'])+(' · УСТАРЕЛО' if view['progress_stale'] else ''),False),('',False)])
                    checkpoint=view['checkpoint']
                    if checkpoint:
                        rows.extend([('Последняя контрольная точка: '+age(checkpoint['at']),False),('Записано: '+checkpoint.get('summary',''),False),('Следующий записанный шаг: '+checkpoint.get('next','не указан'),False),('Проверки: '+checkpoint.get('checks','не указаны'),False),('',False)])
                    elif view['process']=='работает':rows.append(('Текущий этап ещё не записан координатором.',False))
                for task in view['active']+view['completed']+view['cancelled']:
                    label={'running':'В работе','blocked':'Заблокировано','queued':'В очереди','review':'На проверке','done':'Завершено','cancelled':'Отменено'}.get(task['status'],task['status'])
                    rows.extend([(label+' — '+task['title'],True),('Результат / прогресс: '+(task['result'] or 'Отчёт ещё не получен'),False)])
                    evidence=task.get('evidence',{})
                    rows.append(('Артефактов: '+str(len(evidence.get('artifacts',[])))+' · Проверка: '+str((evidence.get('verification') or {}).get('status','не запускалась')),False))
                    check=evidence.get('verification')
                    if check and not check['current_submission']:rows.append(('Проверка относится к предыдущей сдаче или плану; требуется повторная проверка',False))
                    accepted=evidence.get('acceptance')
                    rows.append(('Приёмка записана: '+accepted['agent']+(' · прежний координатор' if not accepted['current_generation'] else '') if accepted else ('Старая запись done: приёмка не подтверждена' if task['status']=='done' else 'Приёмка координатором: ожидается'),False))
                    report=state.get('reports',{}).get(task['id'],{})
                    if report.get('next'):rows.append(('Дальше: '+report['next'],False))
                    worker=state['workers'].get(task['id'],{})
                    rows.extend([('Отчёт: '+age(worker.get('report_at')),False),('ID: '+task['id']+' · панель: '+(worker.get('pane') or '—'),False),('',False)])
                if not view['active'] and not view['completed']:rows.append(('Заданий пока нет — координатор ещё не назначил работу.',False))
                for check in view['readiness']:
                    label={'ready':'Ответ модели подтверждён','quota_blocked':'Лимит исчерпан','blocked':'Нужно подтверждение','busy':'Занят','unconfirmed':'Ответ не подтверждён','not_running':'Не запущен','unknown':'Не проверен'}.get(check['status'],check['status'])
                    rows.append(('Проверка готовности: '+label+' · '+age(check['checked']),False))
                wrapped=[(part,bold) for line,bold in rows for part in (textwrap.wrap(line,max(10,width-6)) or [''])]
                capacity=area-2;offset=min(offset,max(0,len(wrapped)-capacity))
                frame(top,2,area,width,('👑 ' if view['leader'] else '')+view['name']+(' · Координатор' if view['leader'] else '')+' / Подробный отчёт',col)
                for i,(line,bold) in enumerate(wrapped[offset:offset+capacity]):vp(top+1+i,5,line,curses.A_BOLD if bold else 0,width-6)
            else:
                columns=2 if w>=132 else 1
                gap=2;cardwidth=(w-4-gap*(columns-1))//columns;cardheight=16
                total=((len(views)+columns-1)//columns)*(cardheight+1)
                offset=min(offset,max(0,total-area))
                def body(y,x,label,text,lines=1,bold=False):
                    wrapped=textwrap.wrap(label+text,max(10,cardwidth-6)) or ['']
                    for n,line in enumerate(wrapped[:lines]):
                        if n==lines-1 and len(wrapped)>lines:line=line[:max(1,cardwidth-9)]+'…'
                        vp(y+n,x,line,curses.A_BOLD if bold else 0,cardwidth-6)
                for i,view in enumerate(views):
                    y=top+(i//columns)*(cardheight+1)-offset;x=2+(i%columns)*(cardwidth+gap);col=palette[view['agent']]
                    title=('› ' if i==selected else '')+('👑 ' if view['leader'] else '')+view['name']+(' · Координатор' if view['leader'] else '')
                    frame(y,x,cardheight,cardwidth,title,col)
                    hits.append((max(top,y),min(bottom-1,y+cardheight-1),x,x+cardwidth-1,i))
                    body(y+1,x+3,'',view['status'],bold=True)
                    current=view['current'];recent=view['recent']
                    progress=view['progress']
                    body(y+3,x+3,'СЕЙЧАС: ',view['activity'] if progress else (current['title'] if current else view['activity']),2)
                    checkpoint=view['checkpoint']
                    summary=(current['result'] or 'Исполнитель ещё не прислал отчёт') if current else ('Последняя запись '+age(checkpoint.get('at'))+': '+checkpoint.get('summary','') if checkpoint else ('Терминал активен; текущий этап ещё не записан в Bridge' if view['process']=='работает' else 'Нет выполняемой задачи'))
                    if progress:summary=('Устаревший отчёт: ' if view['progress_stale'] else '')+progress['completed']
                    body(y+6,x+3,'СДЕЛАНО: ' if progress else 'ПРОГРЕСС: ',summary,2)
                    next_text=progress['next_step'] if progress else (view['next'] or ('Ожидает проверки координатором' if current and current['status']=='review' else ('Следующий шаг не указан' if current else ('Из последней записи: '+checkpoint['next'] if checkpoint.get('next') else ('Координатор ещё не записал следующий этап' if view['process']=='работает' else 'Ожидает нового задания')))))
                    body(y+9,x+3,'ДАЛЬШЕ: ',next_text,1)
                    body(y+11,x+3,'СДЕЛАНО: ',recent['title'] if recent else 'Завершённых задач пока нет',1)
                    body(y+12,x+3,'',recent['result'] if recent and recent['result'] else '',2)
                    fresh=' · устарел' if view['report_at'] and time.time()-view['report_at']>300 else ''
                    vp(y+14,x+3,('Отчёт: '+age(progress['updated'])+(' · устарел' if view['progress_stale'] else '') if progress else ('Отчёт: '+age(view['report_at'])+fresh if current else 'Запись: '+age(checkpoint.get('at') or view['updated_at'])))+' · CLI: '+view['process'],curses.A_DIM,cardwidth-6)
            put(h-3,3,notice or ('Переключаю координатора…' if switching else 'Выбери карточку: Tab или клик. C — сделать координатором.'),curses.A_DIM,button_x-5)
            put(h-3,button_x,'[ 👑 Сделать координатором ]',accent|curses.A_BOLD)
        put(h-2,3,'Tab Агент · C Координатор · Enter Отчёт · ↑↓ Прокрутка · R Обновить · Q Выход',accent)
        screen.refresh();key=screen.getch()
        if key==curses.KEY_MOUSE:
            try:
                _,mx,my,_,pressed=curses.getmouse()
                if pressed & (curses.BUTTON1_CLICKED|curses.BUTTON1_PRESSED):
                    if my==h-3 and mx>=button_x:key=ord('c')
                    else:
                        for lo,hi,left,right,index in hits:
                            if lo<=my<=hi and left<=mx<=right:selected=index;break
            except curses.error:pass
        if key in (ord('c'),ord('C'),ord('с'),ord('С')) and 'error' not in state and not switching:
            target=names[selected]
            if target==state.get('designated_coordinator'):notice='Этот агент уже назначен координатором'
            else:
                request,message=switch_dialog(screen,project,state,target)
                if request:switching=switch_pool.submit(panel_switch,*request);notice=''
                else:notice=message
            continue
        if key in (ord('q'),ord('Q')):
            switch_pool.shutdown(wait=False);return
        if key==27:
            if details:details=False;offset=0
            else:
                switch_pool.shutdown(wait=False);return
        if key==9:
            selected=(selected+1)%len(names);offset=0
            if not details:
                columns=2 if w>=132 else 1
                row=(selected//columns)*17
                offset=max(0,row+16-max(1,h-11))
        if key in (10,13,curses.KEY_ENTER):details=not details;offset=0
        if key==curses.KEY_DOWN:offset+=1
        if key==curses.KEY_UP:offset=max(0,offset-1)
        if key==curses.KEY_NPAGE:offset+=10
        if key==curses.KEY_PPAGE:offset=max(0,offset-10)
        if key in (ord('r'),ord('R')):updated=process_updated=float('-inf')



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
