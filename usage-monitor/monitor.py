#!/usr/bin/env python3
"""Read-only local usage dashboard. Never reads credentials or sends prompts."""
import curses
import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
import concurrent.futures
import unicodedata
from providers import collect

STATE = Path.home() / '.local/state/herdr-usage'
NAMES = ['Codex', 'Claude', 'Muse', 'Grok', 'Antigravity']


def lines(path, tail=4_000_000):
    try:
        with path.open('rb') as f:
            size = path.stat().st_size
            offset = max(0, size-tail)
            f.seek(offset)
            if offset:
                f.readline()
            for raw in f:
                try:
                    yield json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue
    except OSError:
        return


def latest(root, pattern):
    try:
        return sorted(root.rglob(pattern), key=lambda p:p.stat().st_mtime, reverse=True)[:20]
    except OSError:
        return []


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def codex_record(path):
    record = None
    cwd = None
    model = None
    for event in lines(path):
        payload = event.get('payload') or {}
        if event.get('type') == 'session_meta':
            cwd = payload.get('cwd')
        if event.get('type') == 'turn_context':
            model = payload.get('model', model)
        if payload.get('type') != 'token_count':
            continue
        info = payload.get('info') or {}
        usage = info.get('total_token_usage') or {}
        last = info.get('last_token_usage') or {}
        capacity = info.get('model_context_window')
        # Last request total is a measured proxy, not the live context after compaction.
        observed = last.get('total_tokens')
        record = dict(agent='Codex', source=str(path), updated=event.get('timestamp'),
                      cwd=cwd, model=model, input=usage.get('input_tokens'),
                      output=usage.get('output_tokens'), cached=usage.get('cached_input_tokens'),
                      context_size=capacity, context_observed=observed,
                      context_estimate=True, limits=payload.get('rate_limits') or {})
    return record


def claude_record(data):
    ctx = data.get('context_window') or {}
    return dict(agent='Claude', source='Claude statusline', updated=data.get('_observed_at'),
                cwd=(data.get('workspace') or {}).get('current_dir'),
                model=(data.get('model') or {}).get('display_name'),
                input=ctx.get('total_input_tokens'), output=ctx.get('total_output_tokens'),
                cached=(ctx.get('current_usage') or {}).get('cache_read_input_tokens'),
                context_size=ctx.get('context_window_size'), context_pct=ctx.get('used_percentage'),
                context_remaining_pct=ctx.get('remaining_percentage'),
                cost=(data.get('cost') or {}).get('total_cost_usd'),
                limits=data.get('rate_limits') or {})


def snapshots():
    records = {}
    for path in latest(Path.home()/'.codex/sessions', '*.jsonl'):
        record = codex_record(path)
        if record and (not records.get('Codex') or (record.get('updated') or '') > (records['Codex'].get('updated') or '')):
            records['Codex'] = record
    for path in latest(STATE, 'claude-*.json'):
        try:
            record = claude_record(json.loads(path.read_text()))
            if not records.get('Claude') or (record.get('updated') or '') > (records['Claude'].get('updated') or ''):
                records['Claude'] = record
        except (OSError, ValueError):
            pass
    live_file = STATE / 'providers.json'
    try:
        live = json.loads(live_file.read_text())
    except (OSError, ValueError):
        live = {}
    for name, extra in live.items():
        base = records.setdefault(name, {'agent': name})
        if extra.get('error'):
            base['error'] = extra['error']
            base['attempted_at'] = extra.get('attempted_at')
        else:
            # Usage totals stay local; quota timestamps identify their own observation.
            for key in ('limits', 'windows', 'source', 'updated'):
                if key in extra:
                    base['quota_'+key if key in ('source', 'updated') else key] = extra[key]
            base['plan'] = extra.get('plan')
    return records


def refresh_live():
    data = collect()
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = STATE / 'providers.json'
    tmp = STATE / ('providers.'+str(os.getpid())+'.tmp')
    with tmp.open('w') as f:
        os.chmod(tmp, 0o600)
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, dest)


def fmt(value):
    return f'{value:,.0f}' if number(value) else '—'


def progress(value, width=18):
    if not number(value):
        return '░' * width + '  нет данных'
    value = max(0, min(100, value))
    filled = int(width * value / 100 + .5)
    return '█' * filled + '░' * (width-filled) + f'  {value:.1f}%'


def quotas(limits):
    result = []
    for key in ('primary', 'secondary', 'five_hour', 'seven_day', 'spend_limit'):
        item = limits.get(key)
        if not isinstance(item, dict):
            continue
        used = item.get('used_percent', item.get('used_percentage'))
        if not number(used):
            continue
        minutes = item.get('window_minutes')
        label = ({300: '5 часов', 10080: '7 дней'}.get(minutes, f'{minutes:g} мин')
                 if number(minutes) else {'five_hour':'5 часов', 'seven_day':'7 дней',
                     'spend_limit':'Бюджет'}.get(key, key))
        reset = item.get('resets_at')
        when = dt.datetime.fromtimestamp(reset).astimezone().strftime('%d.%m %H:%M') if number(reset) else '—'
        result.append((label, used, when))
    return result


def quota_lines(limits):
    return [f'{label}: {progress(used)} использовано · осталось {max(0,100-used):.1f}% · сброс {when}'
            for label,used,when in quotas(limits)]


def details(name, record):
    if not record:
        return ['Нет источника данных.', 'Claude: данные появятся после ответа в новой сессии.' if name=='Claude' else 'Подключение этого агента пока не реализовано.']
    result=[f"Тариф: {record.get('plan') or 'источник не сообщает'}", f"Модель: {record.get('model') or '—'}", f"Проект: {record.get('cwd') or 'не определён'}",
            f"Обновлено: {record.get('updated') or '—'}", '',
            f"Вход за сессию: {fmt(record.get('input'))}", f"Выход за сессию: {fmt(record.get('output'))}"]
    result += [f"Кеш {'последнего запроса' if name=='Claude' else 'за сессию'}: {fmt(record.get('cached'))}"]
    if number(record.get('context_pct')):
        result += [f"Контекст: {progress(record['context_pct'])} · окно {fmt(record.get('context_size'))}"]
    elif number(record.get('context_observed')):
        result += [f"Последний запрос: {fmt(record['context_observed'])} · окно {fmt(record.get('context_size'))}",
                   'Это снимок запроса, а не точный остаток текущего контекста.']
    if number(record.get('cost')):
        result += [f"Стоимость сессии по данным CLI: ${record['cost']:.4f}"]
    result += ['', 'КВОТЫ — ПОСЛЕДНИЙ СНИМОК'] + (window_details(record) or ['Квоты не переданы источником.'])
    result += ['', 'Источник квот: ' + record.get('quota_source', record.get('source', '—')), 'Квоты обновлены: ' + record.get('quota_updated', record.get('updated', '—') or '—')]
    if record.get('error'):
        result += [record['error']]
    return result


def all_windows(record):
    result = quotas((record or {}).get('limits') or {})
    for item in (record or {}).get('windows') or []:
        reset = item.get('resets_at')
        when = dt.datetime.fromtimestamp(reset).astimezone().strftime('%d.%m %H:%M') if number(reset) else '—'
        if number(item.get('used')):
            label=item['label'].replace('Gemini Models','Antigravity: Gemini').replace('Claude and GPT models','Antigravity: Claude и GPT')
            result.append((label, item['used'], when))
    return result


def window_details(record):
    result = []
    for label, used, when in all_windows(record):
        result.extend([label, progress(used, 20) + ' использовано', 'Сброс: ' + when])
    return result


def monitor_project():
    value=os.environ.get('BRIDGE_PROJECT')
    if value:return Path(value).expanduser().resolve()
    try:
        binary=__import__('shutil').which('herdr') or '/opt/homebrew/bin/herdr'
        response=__import__('subprocess').run([binary,'agent','list'],capture_output=True,text=True,timeout=3)
        agents=json.loads(response.stdout)['result']['agents']
        roots={a.get('cwd') for a in agents if a.get('cwd')}
        if len(roots)==1:return Path(roots.pop()).resolve()
    except Exception:pass
    return None


def monitor_leader(project):
    if not project:return None
    try:
        import sys,sqlite3
        sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'universal-bridge'))
        from project_store import state_dir
        from bridge import designated_coordinator
        with sqlite3.connect((state_dir(project)/'state.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            return designated_coordinator(db)
    except Exception:return None


def usage_card(name,record):
    record=record or {}
    rows=[('Тариф: '+(record.get('plan') or 'источник не сообщает'),True),
          ('Модель: '+(record.get('model') or 'не передана источником'),False),('',False)]
    windows=all_windows(record)
    if windows:
        for label,used,reset in windows:
            rows.extend([(label,True),(progress(used,18)+' использовано · сброс '+reset,False)])
    else:rows.append(('Квоты: нет подтверждённых данных',False))
    rows.extend([('',False),('ТОКЕНЫ СЕССИИ',True),
        ('Вход '+fmt(record.get('input'))+'   Выход '+fmt(record.get('output'))+'   Кеш '+fmt(record.get('cached')),False)])
    if name=='Claude':rows.append(('Кеш указан для последнего запроса',False))
    if number(record.get('context_pct')):
        rows.append(('Контекст: '+progress(record['context_pct'],14)+' · окно '+fmt(record.get('context_size')),False))
    elif number(record.get('context_observed')):
        rows.append(('Последний запрос '+fmt(record['context_observed'])+' / окно '+fmt(record.get('context_size')),False))
        rows.append(('Это снимок запроса, не точный остаток контекста',False))
    else:rows.append(('Контекст: источник не сообщает',False))
    rows.append(('Стоимость: '+(f"${record['cost']:.4f}" if number(record.get('cost')) else 'не передана'),False))
    raw=record.get('quota_updated') or record.get('updated')
    try:
        parsed=dt.datetime.fromisoformat(raw.replace('Z','+00:00'))
        stamp=parsed.astimezone().strftime('%d.%m %H:%M:%S')
        elapsed=(dt.datetime.now(dt.timezone.utc)-parsed).total_seconds()
        if elapsed>300:stamp+=' · устарело'
    except (ValueError,AttributeError,TypeError):stamp='ещё нет обновлений'
    source=record.get('quota_source') or record.get('source') or 'не подключён'
    if source.startswith('/'):source='Локальный журнал '+name
    rows.extend([('',False),('Квоты обновлены: '+stamp,False),('Источник: '+source,False)])
    if record.get('error'):rows.append(('ВНИМАНИЕ: '+record['error'],True))
    return rows


def dashboard(screen):
    import textwrap
    screen.keypad(True);screen.timeout(400)
    palette={name:0 for name in NAMES}
    if curses.has_colors():
        curses.start_color();curses.use_default_colors()
        colors=[81,213,114,215,111] if curses.COLORS>=256 else [curses.COLOR_CYAN,curses.COLOR_MAGENTA,curses.COLOR_GREEN,curses.COLOR_YELLOW,curses.COLOR_BLUE]
        for i,(name,fg) in enumerate(zip(NAMES,colors),1):
            curses.init_pair(i,fg,-1);palette[name]=curses.color_pair(i)
        curses.init_pair(6,-1,-1);screen.bkgd(' ',curses.color_pair(6))
    try:curses.curs_set(0)
    except curses.error:pass
    offset=0;records=snapshots();refreshed=float('-inf');next_live=0
    project=monitor_project();leader=None;leader_at=float('-inf')
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=1);future=None
    try:
        while True:
            clock=time.monotonic()
            if future is not None and future.done():
                try:future.result()
                except Exception:pass
                future=None;refreshed=float('-inf')
            if clock>=next_live and future is None:future=pool.submit(refresh_live);next_live=clock+120
            if clock-refreshed>5:records=snapshots();refreshed=clock
            if clock-leader_at>2:leader=monitor_leader(project);leader_at=clock
            order=sorted(NAMES,key=lambda name:0 if name.lower()==leader else 1)
            h,w=screen.getmaxyx();screen.erase()
            def put(y,x,text,attr=0,limit=None):
                if not (0<=y<h-1 and 0<=x<w-1):return
                budget=min(w-x-1,limit if limit is not None else w);out='';used=0
                for ch in str(text):
                    size=0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in 'WF' else 1)
                    if used+size>budget:break
                    out+=ch;used+=size
                try:screen.addstr(y,x,out,attr)
                except curses.error:pass
            accent=palette['Codex']
            put(1,3,'N I P P R   /   КВОТЫ И РАСХОД',accent|curses.A_BOLD)
            put(2,3,('Проект: '+project.name+' · ' if project else '')+'Все агенты на одной странице',curses.A_DIM)
            put(4,3,'Полосы показывают ИСПОЛЬЗОВАНО: 0% свободно → 100% квота исчерпана')
            put(5,3,'Обновляю источники…' if future else 'Опрос сервисов: 2 минуты · локальные данные: 5 секунд',curses.A_DIM)
            if w<64 or h<22:put(8,3,'Разверни терминал хотя бы до 64 × 22.')
            else:
                top=7;bottom=h-4;area=bottom-top
                columns=3 if w>=200 else (2 if w>=132 else 1)
                gap=2;width=(w-4-gap*(columns-1))//columns
                rendered=[]
                for name in order:
                    lines=[(part,bold) for line,bold in usage_card(name,records.get(name))
                           for part in (textwrap.wrap(line,max(10,width-6)) or [''])]
                    rendered.append((name,lines))
                row_heights=[max(len(lines)+3 for _,lines in rendered[i:i+columns]) for i in range(0,len(rendered),columns)]
                total=sum(height+1 for height in row_heights)
                offset=min(offset,max(0,total-area))
                def vp(y,x,text,attr=0,limit=None):
                    if top<=y<bottom:put(y,x,text,attr,limit)
                y=top-offset
                for group,rowheight in zip(range(0,len(rendered),columns),row_heights):
                    for pos,(name,lines) in enumerate(rendered[group:group+columns]):
                        x=2+pos*(width+gap);color=palette[name]
                        vp(y,x,'╭'+'─'*(width-2)+'╮',color)
                        for yy in range(y+1,y+rowheight-1):vp(yy,x,'│',color);vp(yy,x+width-1,'│',color)
                        vp(y+rowheight-1,x,'╰'+'─'*(width-2)+'╯',color)
                        title=('👑 ' if name.lower()==leader else '')+name+(' · Координатор' if name.lower()==leader else '')
                        vp(y,x+2,' '+title+' ',color|curses.A_BOLD,width-5)
                        for n,(line,bold) in enumerate(lines):vp(y+1+n,x+3,line,curses.A_BOLD if bold else 0,width-6)
                    y+=rowheight+1
                put(h-3,3,'Токены — из последней локальной сессии; квоты — снимки источников, не общий баланс.',curses.A_DIM)
            put(h-2,3,'↑↓ / PgUp PgDn Прокрутка страницы · R Обновить · Q / Esc Закрыть',accent)
            screen.refresh();key=screen.getch()
            if key in (ord('q'),ord('Q'),27):return
            if key==curses.KEY_UP:offset=max(0,offset-1)
            if key==curses.KEY_DOWN:offset+=1
            if key==curses.KEY_NPAGE:offset+=10
            if key==curses.KEY_PPAGE:offset=max(0,offset-10)
            if key in (ord('r'),ord('R')):refreshed=float('-inf');next_live=0
    finally:pool.shutdown(wait=False)



if __name__=='__main__':
    if '--refresh' in sys.argv:
        refresh_live()
    elif '--json' in sys.argv:
        print(json.dumps(snapshots(),ensure_ascii=False,indent=2))
    else:
        try: curses.wrapper(dashboard)
        except KeyboardInterrupt: pass
