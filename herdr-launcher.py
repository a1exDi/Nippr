#!/usr/bin/python3
"""Interactive project and agent launcher for Herdr (macOS / Python 3)."""
import concurrent.futures
import curses
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

AGENTS = [('Muse', 'muse'), ('Grok', 'grok'), ('Antigravity', 'agy'),
          ('Codex', 'codex'), ('Claude', 'claude')]
SKIP = {'.git', 'node_modules', '.venv', 'venv', 'build', 'dist', '.next',
        'Library', '.cache', 'Pods', '.build'}


def projects():
    roots = [Path.home() / 'Documents' / name for name in ('ChatGPT', 'DEV', 'Codex')]
    config = Path.home() / '.config/herdr-launcher/roots.txt'
    if config.exists():
        roots = [Path(p.strip()).expanduser() for p in config.read_text().splitlines()
                 if p.strip() and not p.lstrip().startswith('#')]
    found = set()
    markers = {'.git', 'package.json', 'pyproject.toml', 'Cargo.toml', 'Package.swift'}
    for root in roots:
        if not root.is_dir():
            continue
        if root.name != 'Codex':
            found.update(p.resolve() for p in root.iterdir()
                         if p.is_dir() and not p.name.startswith('.') and p.name not in SKIP)
        for folder, dirs, files in os.walk(root):
            depth = len(Path(folder).relative_to(root).parts)
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in SKIP and not d.startswith(('node_modules', 'dependency-recovery'))]
            if depth >= 4:
                dirs[:] = []
            if (markers & set(files) or (Path(folder) / '.git').exists()
                    or any(f.endswith('.xcodeproj') for f in dirs)):
                found.add(Path(folder).resolve())
    return sorted(found, key=lambda p: str(p).lower())


def recent_sessions(executable):
    """Read Herdr's registry; never start a server during discovery."""
    result = subprocess.run([executable, 'session', 'list', '--json'],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Не удалось получить список сессий')
    sessions = []
    for entry in json.loads(result.stdout).get('sessions', []):
        name = entry.get('name')
        if not isinstance(name, str) or not name:
            continue
        snapshot = Path(entry['session_dir']) / 'session.json'
        workspaces, updated = [], 0
        try:
            data = json.loads(snapshot.read_text())
            workspaces = data.get('workspaces', [])
            updated = snapshot.stat().st_mtime
        except (OSError, ValueError):
            pass
        titles = [w.get('custom_name') or Path(w.get('identity_cwd') or '').name
                  for w in workspaces if isinstance(w, dict)]
        sessions.append(dict(name=name, running=bool(entry.get('running')),
                             project=', '.join(t for t in titles if t) or name,
                             updated=updated))
    return sorted(sessions, key=lambda item: (item['updated'], item['name']), reverse=True)[:20]


def session_label(item):
    stamp = time.strftime('%d.%m %H:%M', time.localtime(item['updated'])) if item['updated'] else 'дата неизвестна'
    status = 'Работает' if item['running'] else 'Остановлена'
    return f"{item['project']} · {status} · {stamp} · {item['name']}"


def choose_launch(screen, paths, sessions):
    if sessions:
        while True:
            index = menu(screen, 'Последние сессии',
                         [session_label(item) for item in sessions] + ['＋ Новая сессия'],
                         sessions=True)
            if index < len(sessions):
                return sessions[index]['name']
            try:
                return choose(screen, paths)
            except BackToSessions:
                continue
    return choose(screen, paths)


class BackToSessions(Exception):
    pass


class BackToProjects(Exception):
    pass


def menu(screen, title, options, multiple=False, project=None, coordinator=False, sessions=False):
    import textwrap
    import unicodedata
    screen.keypad(True)
    try:
        curses.curs_set(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(180)
    except curses.error:
        pass
    colors = [0] * 5
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        for i, (fg, bg) in enumerate([(curses.COLOR_CYAN, -1), (curses.COLOR_CYAN, -1),
                                       (curses.COLOR_GREEN, -1), (curses.COLOR_WHITE, -1)], 1):
            curses.init_pair(i, fg, bg)
            colors[i] = curses.color_pair(i)
    current, selected, query = 0, set(), ''
    icons = ['🎨', '🚀', '🌌', '💻', '🧠']
    while True:
        visible = [i for i, text in enumerate(options) if query.lower() in text.lower()]
        current = min(current, max(0, len(visible) - 1))
        screen.erase()
        height, width = screen.getmaxyx()
        def put(y, x, text, attr=0, limit=None):
            if y < 0 or y >= height - 1 or x < 0 or x >= width - 1:
                return
            budget = min(width - x - 1, limit if limit is not None else width)
            out, used = '', 0
            for char in str(text):
                size = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in 'WF' else 1)
                if used + size > budget:
                    break
                out += char
                used += size
            try:
                screen.addstr(y, x, out, attr)
            except curses.error:
                pass
        def box(y, x, h, w, label):
            put(y, x, '╭' + '─' * (w - 2) + '╮', colors[1])
            for yy in range(y + 1, y + h - 1):
                put(yy, x, '│', colors[1])
                put(yy, x + w - 1, '│', colors[1])
            put(y + h - 1, x, '╰' + '─' * (w - 2) + '╯', colors[1])
            put(y, x + 3, ' ' + label + ' ', colors[1] | curses.A_BOLD, w - 6)
        if width < 64 or height < 20:
            put(1, 2, 'Разверни окно терминала: нужно хотя бы 64 × 20.', curses.A_BOLD)
            put(3, 2, 'Esc — выход. Интерфейс подстроится автоматически.')
            screen.refresh()
            if screen.get_wch() == '\x1b':
                raise KeyboardInterrupt
            continue
        put(1, 3, '🐑  H E R D R   /   LAUNCHPAD', colors[1] | curses.A_BOLD)
        put(3, 3, 'Последние сессии — продолжить работу' if sessions else '① Проект   →   ② Команда   →   ③ Координатор', curses.A_BOLD)
        put(4, 3, 'Выбери сессию или создай новую' if sessions else 'Выбери, кто будет управлять проектом' if coordinator else ('Собери команду для проекта' if multiple else 'Выбери, над чем будем работать'), colors[4])
        top, panel_h = 6, height - 11
        left_w = int((width - 6) * .60) if width >= 96 else width - 4
        right_x = left_w + 3
        row_step = 3 if height >= 28 else 2
        capacity = max(1, (panel_h - 4) // row_step)
        start = max(0, current - capacity + 1)
        box(top, 2, panel_h, left_w, '↩ Последние сессии' if sessions else '👑 Координатор' if coordinator else ('🤖 Агенты' if multiple else '📁 Проекты'))
        put(top + 1, 4, f'Выбрано: {len(selected)} / {len(options)}    [A] Все' if multiple else '⌕ Поиск: ' + (query or 'начни печатать…'), colors[1], left_w - 4)
        hits = []
        for pos in range(start, min(len(visible), start + capacity)):
            index = visible[pos]
            y = top + 3 + (pos - start) * row_step
            attr = colors[2] | curses.A_BOLD if pos == current else 0
            put(y, 4, ' ' * (left_w - 4), attr)
            text = options[index] if multiple or coordinator or sessions else Path(options[index]).name
            prefix = ('☑ ' if index in selected else '☐ ') + icons[index % len(icons)] + '  ' if multiple else ('👑  ' if coordinator else '📁  ')
            put(y, 5, prefix + text, attr, left_w - 6)
            if row_step == 3:
                detail = 'Enter — открыть выбранную сессию' if sessions else 'Управление проектом через Bridge' if coordinator else ('Готов к запуску в отдельной панели' if multiple else str(Path(options[index]).parent).replace(str(Path.home()), '~', 1))
                put(y + 1, 8, detail, colors[1], left_w - 10)
            hits.append((y, y + row_step - 1, index, pos))
        if not visible:
            put(top + 4, 5, 'Ничего не найдено. Измени поиск.')
        if width >= 96:
            rw = width - right_x - 2
            box(top, right_x, panel_h, rw, '✦ Подробности')
            detail_lines = []
            if sessions:
                detail_lines = textwrap.wrap(options[visible[current]], max(10, rw - 6)) if visible else []
                detail_lines += ['', 'Работает: подключение', 'к существующим панелям.', '', 'Остановлена: восстановление', 'сохранённой раскладки;', 'процессы агентов могли', 'завершиться.', '', 'Дата — сохранение раскладки.']
            elif coordinator:
                detail_lines = ['КООРДИНАТОР', options[visible[current]] if visible else '—', '', 'ПРОЕКТ', str(project or ''), '', 'Читает состояние Bridge,', 'принимает управление,', 'ведёт задачи и передаёт роль.', '', 'Действующий координатор', 'не будет заменён молча.']
            elif multiple:
                detail_lines = ['ПРОЕКТ', Path(project).name if project else 'Выбранный проект', '', 'ТВОЯ КОМАНДА']
                detail_lines += [AGENTS[i][0] for i in sorted(selected)] or ['Пока никто не выбран']
                detail_lines += ['', 'Каждый агент получит свою', 'панель в общей сессии Herdr.']
            elif visible:
                path = Path(options[visible[current]])
                detail_lines = ['ВЫБРАННЫЙ ПРОЕКТ', path.name, '', 'РАСПОЛОЖЕНИЕ']
                detail_lines += textwrap.wrap(str(path), max(10, rw - 6), break_long_words=True)
                detail_lines += ['', 'Далее — выбор AI-агентов.']
            for n, text in enumerate(detail_lines[:panel_h - 3]):
                put(top + 2 + n, right_x + 3, text, colors[1] if text.isupper() else 0, rw - 6)
        button = '  Открыть →  ' if sessions else '  🚀 Запустить Herdr  ' if coordinator else '  Продолжить →  '
        by = height - 4
        bx = max(3, width - len(button) - 6)
        enabled = bool(selected) if multiple else bool(visible)
        put(by, 3, (('← Назад · Esc Выход' if coordinator else '← Назад · Esc Выход · Space Выбор') if multiple or coordinator else 'Esc Отмена · ↑↓ Навигация'), colors[1], bx - 5)
        put(by, bx, button, colors[2] | curses.A_BOLD if enabled else curses.A_DIM)
        put(height - 2, 3, 'Клик — выбор  ·  Enter — продолжить  ·  Колесо — прокрутка', colors[1])
        screen.refresh()
        key = screen.get_wch()
        if key == '\x1b':
            raise KeyboardInterrupt
        if key == curses.KEY_MOUSE:
            try:
                _, mx, my, _, state = curses.getmouse()
            except curses.error:
                continue
            if state & getattr(curses, 'BUTTON4_PRESSED', 0):
                current = max(0, current - 1)
                continue
            if state & getattr(curses, 'BUTTON5_PRESSED', 0):
                current = min(max(0, len(visible) - 1), current + 1)
                continue
            if state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_DOUBLE_CLICKED):
                if (multiple or coordinator) and my == by and 3 <= mx < 12:
                    raise BackToProjects
                if my == by and mx >= bx:
                    key = '\n'
                else:
                    for lo, hi, index, pos in hits:
                        if lo <= my <= hi and 2 < mx < left_w + 2:
                            current = pos
                            if multiple:
                                selected.symmetric_difference_update({index})
                            elif state & curses.BUTTON1_DOUBLE_CLICKED:
                                key = '\n'
                            break
        if (multiple or coordinator) and key in (curses.KEY_LEFT, curses.KEY_BACKSPACE, '\x7f', '\b'):
            raise BackToProjects
        if key == curses.KEY_UP:
            current = max(0, current - 1)
        elif key == curses.KEY_DOWN:
            current = min(max(0, len(visible) - 1), current + 1)
        elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
            current = max(0, min(max(0, len(visible) - 1), current + (capacity if key == curses.KEY_NPAGE else -capacity)))
        elif key in ('\n', '\r', curses.KEY_ENTER):
            if multiple and selected:
                return sorted(selected)
            if not multiple and visible:
                return visible[current]
        elif multiple and key in ('a', 'A', 'ф', 'Ф'):
            selected = set() if len(selected) == len(options) else set(range(len(options)))
        elif multiple and key == ' ' and visible:
            selected.symmetric_difference_update({visible[current]})
        elif not multiple and key in ('\x7f', '\b', curses.KEY_BACKSPACE):
            query = query[:-1]
        elif not multiple and isinstance(key, str) and key.isprintable():
            query += key
            current = 0


def choose(screen, paths):
    if not paths:
        raise RuntimeError('Проекты не найдены. Укажи корневые папки в ~/.config/herdr-launcher/roots.txt (по одной на строку).')
    while True:
        try:
            index = menu(screen, 'HERDR — выбери проект', [str(p) for p in paths])
        except KeyboardInterrupt:
            raise BackToSessions
        while True:
            try:
                agents = menu(screen, 'HERDR — выбери агентов', [a[0] for a in AGENTS], True, project=paths[index])
            except BackToProjects:
                break
            team = [AGENTS[i] for i in agents]
            try:
                leader = menu(screen, 'HERDR — координатор', [a[0] for a in team],
                              project=paths[index], coordinator=True)
            except BackToProjects:
                continue
            return paths[index], team, team[leader][1]


def prepare_bridge(project, coordinator, session, agents):
    bridge_root = Path(__file__).resolve().parent / 'universal-bridge'
    script = bridge_root / 'bridge.py'
    guide = bridge_root / 'AGENT_BRIDGE.md'
    if not script.is_file() or not guide.is_file():
        raise RuntimeError('Universal Bridge files missing: '+str(bridge_root))
    result = subprocess.run([sys.executable, str(script), '--project', str(project), 'init',
        '--goal', 'Ожидание задачи пользователя; выбран координатор через Herdr Launchpad.'],
        capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('Bridge init: '+(result.stderr or result.stdout))
    name = 'antigravity' if coordinator == 'agy' else coordinator
    prompt = (f'You are the selected coordinator ({name}) for this project: {project}. '
              f'Read {guide}. Use python3 {script} --project "{project}" status and context. '
              f'If there is no active owner, claim --agent {name}; if its lease expired, recover with a reason. '
              'Never steal a live lease or a handoff reserved for another agent; explain the conflict to the user. '
              'Keep the returned token private and heartbeat before expiry. '
              'Read project instructions, inspect actual files and existing processes, and record checkpoints. '
              'Before each substantial step, after its result, and at least once per minute during long work, use progress --agent '+name+' --current CURRENT_TASK --done ACTUAL_COMPLETED --next NEXT_STEP --checks REAL_CHECKS --state working in Russian. This command loads the reporting capability internally and renews an active lease. Never extract tokens. Use --state blocked or done when appropriate; do not report invented progress. '
              'Before assigning work, read status revision and run reconcile --revision REV --evidence with actual checks. '
              'Before delegating, split the objective into non-overlapping roles and explicit file scopes; shared config/lockfiles are handled sequentially by one owner. Use allocate --plan FILE to validate the entire wave, then allocate --plan FILE --send to register and send it atomically at the reservation level. Use dispatch --title TITLE --assignee AGENT --resources FILES --session SESSION --pane PANE --instructions-file FILE for all executor work: it registers, reserves and delivers report instructions. Workers use worker-report independently. '
              'Other selected agents are available as executors in this Herdr session: '
              + ', '.join(kind for _, kind in agents) + '. '
              'Do not begin source changes or invent a project objective; await the user task. '
              'On quota exhaustion, checkpoint and handoff to another available agent when possible. '
              'If unable to claim, report that coordination is pending rather than claiming ownership.')
    located=subprocess.run([sys.executable,str(script),'--project',str(project),'where'],capture_output=True,text=True,check=True)
    folder=Path(json.loads(located.stdout)['state_dir'])
    dest = folder / (session + '.instructions.txt')
    with dest.open('w') as f:
        os.chmod(dest, 0o600)
        f.write(prompt+'\n')
    return prompt


def finish_setup(payload):
    base, agents, panes = payload['base'], payload['agents'], payload['panes']
    coordinator_delivered=[]
    if payload.get('project'):
        guard=Path(__file__).resolve().parent/'universal-bridge/permission_guard.py'
        subprocess.Popen([sys.executable,str(guard),'--project',payload['project'],'--session',base[2]],
            stdin=subprocess.DEVNULL,start_new_session=True)
    def start(item):
        (label, kind), pane = item
        try:
            result = subprocess.run(base + ['agent', 'start', kind, '--kind', kind, '--pane', pane],
                                    capture_output=True, text=True, timeout=45)
            if result.returncode:
                print(label+': '+(result.stderr or result.stdout), flush=True)
                return
            if kind == payload.get('coordinator'):
                prompted = subprocess.run(base + ['agent', 'prompt', kind, payload['bootstrap']],
                    capture_output=True, text=True, timeout=15)
                if not prompted.returncode:coordinator_delivered.append(True)
                if prompted.returncode:
                    print('Coordinator prompt not delivered; instructions saved in project .agent-bridge: '+(prompted.stderr or prompted.stdout), flush=True)
                subprocess.run(base + ['agent', 'focus', kind], capture_output=True, timeout=10)
        except Exception as exc:
            print(label+': '+str(exc), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as pool:
        list(pool.map(start, zip(agents, panes)))
    if payload.get('project'):
        script=Path(__file__).resolve().parent/'universal-bridge/bridge.py'
        trusted=subprocess.run([sys.executable,str(script),'--project',payload['project'],
            'trust-workspace','--session',base[2]],capture_output=True,text=True,timeout=45)
        print('Workspace trust: '+(trusted.stdout or trusted.stderr),flush=True)
        if payload.get('coordinator') and not coordinator_delivered:
            # Trust confirmation may have unblocked startup; submit bootstrap once ready.
            for attempt in range(10):
                state=subprocess.run(base+['agent','get',payload['coordinator']],capture_output=True,text=True,timeout=5)
                try:ready=json.loads(state.stdout)['result']['agent'].get('interactive_ready')
                except (ValueError,KeyError,TypeError):ready=False
                if ready:
                    delivered=subprocess.run(base+['agent','prompt',payload['coordinator'],payload['bootstrap']],capture_output=True,text=True,timeout=10)
                    print('Coordinator bootstrap after trust: '+('submitted' if delivered.returncode==0 else 'unconfirmed'),flush=True)
                    break
                time.sleep(.5)
    if payload.get('project'):
        script=Path(__file__).resolve().parent/'universal-bridge/bridge.py'
        try:
            health=subprocess.run([sys.executable,str(script),'--project',payload['project'],'health',
                '--session',base[2],'--probe'],capture_output=True,text=True,timeout=60)
            print('Bridge readiness: '+(health.stdout or health.stderr),flush=True)
        except (OSError,subprocess.TimeoutExpired) as exc:
            print('Bridge readiness unconfirmed: '+str(exc),flush=True)
    try:
        result = subprocess.run(base + ['plugin', 'pane', 'open', '--plugin', 'local.usage-monitor',
            '--entrypoint', 'dashboard', '--placement', 'tab', '--no-focus']+(['--env','BRIDGE_PROJECT='+payload['project']] if payload.get('project') else []),
            capture_output=True, text=True, timeout=15)
        if result.returncode:
            print('Usage Monitor: '+(result.stderr or result.stdout), flush=True)
    except Exception as exc:
        print('Usage Monitor: '+str(exc), flush=True)
    if payload.get('project'):
        try:
            result = subprocess.run(base + ['plugin','pane','open','--plugin','local.usage-monitor',
                '--entrypoint','bridge','--placement','tab','--no-focus',
                '--env','BRIDGE_PROJECT='+payload['project']], capture_output=True,text=True,timeout=15)
            if result.returncode: print('Bridge board: '+(result.stderr or result.stdout),flush=True)
        except Exception as exc:
            print('Bridge board: '+str(exc),flush=True)


def bind_runtime(project, session, agents, panes, coordinator=None):
    script=Path(__file__).resolve().parent/'universal-bridge/bridge.py'
    mapping={('antigravity' if kind=='agy' else kind):pane for (_,kind),pane in zip(agents,panes)}
    result=subprocess.run([sys.executable,str(script),'--project',str(project),'register-session',
        '--session',session,'--panes',json.dumps(mapping)]+(['--coordinator','antigravity' if coordinator=='agy' else coordinator] if coordinator else []),capture_output=True,text=True,timeout=15)
    if result.returncode:raise RuntimeError('Bridge runtime registration: '+(result.stderr or result.stdout))
    backup=subprocess.run([sys.executable,str(script),'--project',str(project),'backup'],capture_output=True,text=True,timeout=15)
    if backup.returncode:print('Bridge backup failed; inspect state before relying on recovery.',flush=True)


def main():
    # GUI terminals may start zsh without reading .zprofile. Supply known CLI locations.
    additions = [str(Path.home()/'.local/bin'), str(Path.home()/'.grok/bin'), '/opt/homebrew/bin', '/usr/local/bin']
    os.environ['PATH'] = os.pathsep.join(dict.fromkeys(os.environ.get('PATH', '').split(os.pathsep) + additions))
    if len(sys.argv)>2 and sys.argv[1]=='--finish-setup':
        finish_setup(json.loads(sys.argv[2]))
        return
    paths = projects()
    if '--list-projects' in sys.argv:
        print('\n'.join(map(str, paths)))
        return
    executable = shutil.which('herdr')
    if not executable:
        raise RuntimeError('Herdr не найден. Установи: brew install herdr. Затем снова запусти herdr-start.')
    try:
        sessions = recent_sessions(executable)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        # Do not silently create another session when discovery fails.
        raise RuntimeError('Не удалось проверить последние сессии: ' + str(exc)) from exc
    try:
        choice = curses.wrapper(choose_launch, paths, sessions)
    except BackToSessions:
        raise KeyboardInterrupt
    if isinstance(choice, str):
        os.execv(executable, [executable, 'session', 'attach', choice])
        return
    project, agents, coordinator = choice
    # A unique session avoids mixing new panes with an existing running session.
    session = 'launch-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())
    bootstrap = prepare_bridge(project, coordinator, session, agents)
    base = [executable, '--session', session]
    def call(*args):
        result = subprocess.run(base + list(args), capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Herdr command failed')
        return json.loads(result.stdout)
    logdir = Path.home() / 'Library/Logs/herdr-launcher'
    logdir.mkdir(parents=True, exist_ok=True)
    with (logdir / (session + '.log')).open('a') as log:
        server = subprocess.Popen(base + ['server'], stdout=log, stderr=log, start_new_session=True)
    print('Подготовка сессии:', session, '\nПроект:', project, flush=True)
    for _ in range(100):
        try:
            call('workspace', 'list')
            break
        except (RuntimeError, json.JSONDecodeError):
            if server.poll() is not None:
                raise RuntimeError('Сервер Herdr завершился. Журнал: ' + str(logdir / (session + '.log')))
            time.sleep(.1)
    else:
        raise RuntimeError('Сервер не ответил. Журнал: ' + str(logdir / (session + '.log')))
    result = call('workspace', 'create', '--cwd', str(project), '--label', project.name, '--focus')
    panes = [result['result']['root_pane']['pane_id']]
    for i in range(1, len(agents)):
        result = call('pane', 'split', panes[(i - 1) // 2], '--direction',
                      'right' if i % 2 else 'down', '--no-focus', '--cwd', str(project))
        panes.append(result['result']['pane']['pane_id'])
    bind_runtime(project, session, agents, panes, coordinator)
    # Attach immediately; slow agent readiness and optional plugins cannot block the TUI.
    payload = {'project': str(project), 'base': base, 'agents': agents, 'panes': panes, 'coordinator': coordinator, 'bootstrap': bootstrap}
    with (logdir / (session + '.setup.log')).open('a') as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--finish-setup',
                          json.dumps(payload)], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True)
    os.chdir(project)
    os.execv(executable, base)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nЗапуск отменён.')
    except Exception as exc:
        print('Ошибка:', exc, file=sys.stderr)
        sys.exit(1)
