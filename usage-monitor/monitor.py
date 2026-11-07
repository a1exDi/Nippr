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


def dashboard(screen):
    screen.keypad(True)
    screen.timeout(400)
    color = 0
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        color = curses.color_pair(1)
        curses.init_pair(2, -1, -1)
        screen.bkgd(' ', curses.color_pair(2))
    try: curses.curs_set(0)
    except curses.error: pass
    current, offset = 0, 0
    records = snapshots()
    refreshed, next_live = 0, 0
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = None
    try:
        while True:
            clock = time.monotonic()
            if future is not None and future.done():
                try: future.result()
                except Exception: pass
                future = None
                refreshed = 0
            if clock >= next_live and future is None:
                future = pool.submit(refresh_live)
                next_live = clock + 120
            if clock-refreshed > 5:
                records = snapshots()
                refreshed = clock
            screen.erase()
            h,w = screen.getmaxyx()
            def put(y,x,text,attr=0,limit=None):
                if not (0 <= y < h-1 and 0 <= x < w-1): return
                budget = min(w-x-1, limit if limit is not None else w)
                out, size = '', 0
                for ch in str(text):
                    n = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in 'WF' else 1)
                    if size+n > budget: break
                    out += ch; size += n
                try: screen.addstr(y,x,out,attr)
                except curses.error: pass
            def box(y,x,height,width,title):
                put(y,x,'╭'+'─'*(width-2)+'╮',color)
                for yy in range(y+1,y+height-1):
                    put(yy,x,'│',color);put(yy,x+width-1,'│',color)
                put(y+height-1,x,'╰'+'─'*(width-2)+'╯',color)
                put(y,x+2,' '+title+' ',color|curses.A_BOLD,width-4)
            if w < 76 or h < 24:
                put(2,2,'Разверни терминал минимум до 76 × 24. Q — выход.')
            else:
                put(1,3,'📊  USAGE MONITOR   /   AI ACCOUNTS',color|curses.A_BOLD)
                put(3,3,'0% — квота не израсходована     100% — квота исчерпана')
                put(4,3,'Обновляю сервисы…' if future else 'Квоты: опрос раз в 2 минуты · локальные счётчики: раз в 5 секунд',curses.A_DIM)
                left = min(53, max(32, int(w*.42)))
                right = w-left-6
                ph = h-9
                box(6,2,ph,left,'🤖 Агенты и тарифы')
                box(6,left+4,ph,right,'✦ '+NAMES[current]+' / Подробности')
                step = 7 if ph >= 38 else (6 if ph >= 33 else (5 if ph>=28 else 4))
                capacity = max(1,(ph-2)//step)
                start = max(0,current-capacity+1)
                for pos in range(start,min(len(NAMES),start+capacity)):
                    name=NAMES[pos];record=records.get(name) or {}
                    yy=8+(pos-start)*step
                    attr=color|curses.A_BOLD if pos==current else curses.A_BOLD
                    put(yy,4,('› ' if pos==current else '  ')+name,attr,left-4)
                    put(yy+1,6,'Тариф: '+(record.get('plan') or 'не сообщается'),curses.A_DIM,left-6)
                    windows=all_windows(record)
                    if name=='Antigravity':
                        put(yy+2,6,'Лимиты внутри Antigravity:',0,left-6)
                        if step>=5: put(yy+3,6,'Gemini; Claude и GPT',0,left-6)
                        if step>=6: put(yy+4,6,'Все полосы — справа →',curses.A_DIM,left-6)
                    elif windows:
                        for n,(label,used,_) in enumerate(windows[:max(1,step-3)]):
                            put(yy+2+n,6,label+' '+progress(used,10 if left>=46 else 8),0,left-6)
                    else: put(yy+2,6,'Квота: нет данных',curses.A_DIM,left-6)
                import textwrap
                content=[part for line in details(NAMES[current],records.get(NAMES[current]))
                         for part in (textwrap.wrap(line,max(10,right-4),break_long_words=True) or [''])]
                count=ph-3
                offset=min(offset,max(0,len(content)-count))
                for n,text in enumerate(content[offset:offset+count]):
                    put(8+n,left+6,text,0,right-4)
                if len(content)>count: put(h-4,left+6,'PgUp / PgDn — прокрутить подробности',curses.A_DIM,right-4)
                put(h-2,3,'↑↓ Агент   R Обновить квоты   PgUp/PgDn Детали   Q / Esc Закрыть',color)
            screen.refresh()
            key=screen.getch()
            if key in (ord('q'),ord('Q'),27): return
            if key==curses.KEY_UP: current=max(0,current-1);offset=0
            if key==curses.KEY_DOWN: current=min(len(NAMES)-1,current+1);offset=0
            if key==curses.KEY_NPAGE: offset+=5
            if key==curses.KEY_PPAGE: offset=max(0,offset-5)
            if key in (ord('r'),ord('R')): refreshed=0;next_live=0
    finally:
        pool.shutdown(wait=False)


if __name__=='__main__':
    if '--refresh' in sys.argv:
        refresh_live()
    elif '--json' in sys.argv:
        print(json.dumps(snapshots(),ensure_ascii=False,indent=2))
    else:
        try: curses.wrapper(dashboard)
        except KeyboardInterrupt: pass
