import importlib.util
from pathlib import Path
from unittest.mock import patch
import json
import types
spec = importlib.util.spec_from_file_location('launcher', 'herdr-launcher.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
commands=[]
def run(args, **kwargs):
    commands.append(args)
    result={}
    if 'create' in args:
        result={'root_pane':{'pane_id':'w1:p1'}}
    if 'split' in args:
        result={'pane':{'pane_id':f'w1:p{len(commands)}'}}
    return types.SimpleNamespace(returncode=0,stdout=json.dumps({'result':result}),stderr='')
class StopExec(Exception): pass
with patch.object(m,'bind_runtime'), patch.object(m,'prepare_bridge',return_value='Bootstrap'), patch.object(m,'projects',return_value=[Path.cwd()]), patch.object(m.curses,'wrapper',return_value=(Path.cwd(),m.AGENTS,'claude')), patch.object(m.shutil,'which',return_value='/mock/herdr'), patch.object(m.subprocess,'run',side_effect=run), patch.object(m.subprocess,'Popen'), patch.object(m.os,'execv',side_effect=StopExec), patch.object(m.os,'chdir'), patch.object(m.Path,'open', __import__('unittest.mock',fromlist=['mock_open']).mock_open()):
    try: m.main()
    except StopExec: pass
assert not [x for x in commands if 'start' in x], 'Must attach without waiting for agents'
with patch.object(m.subprocess,'run',side_effect=run):
    m.finish_setup({'base':['/mock/herdr','--session','test'], 'agents':m.AGENTS, 'panes':['w1:p'+str(i) for i in range(1,6)]})
starts=[x for x in commands if 'start' in x]
assert len(starts)==5
assert len([x for x in commands if 'split' in x])==4
for args, (_,kind) in zip(starts,m.AGENTS):
    assert args[args.index('start')+1]==kind
assert all('--session' in x for x in commands)
print('PASS: 5 named agents, 4 splits, selected project, isolated session, attach reached')
class Screen:
    def __init__(self,keys): self.keys=iter(keys)
    def keypad(self,*a): pass
    def erase(self): pass
    def getmaxyx(self): return 32,120
    def addstr(self,*a): pass
    def refresh(self): pass
    def get_wch(self): return next(self.keys)
with patch.object(m.curses,'curs_set'), patch.object(m.curses,'mousemask'), patch.object(m.curses,'mouseinterval'), patch.object(m.curses,'has_colors',return_value=False):
    assert m.menu(Screen(['b','\n']),'Projects',['alpha','beta'])==1
    assert m.menu(Screen([' ',m.curses.KEY_DOWN,' ','\n']),'Agents',['A','B'],True)==[0,1]
    try: m.menu(Screen(['\x1b']),'Cancel',['A'])
    except KeyboardInterrupt: pass
    else: raise AssertionError('Cancel failed')
print('PASS: project search, agent multi-select, cancellation')

with patch.object(m,'menu',side_effect=[0,m.BackToProjects(),1,[0,4],1]):
    project,agents,leader=m.choose(None,[Path('/a'),Path('/b')])
    assert project==Path('/b') and agents==[m.AGENTS[0],m.AGENTS[4]] and leader=='claude'
with patch.object(m.curses,'curs_set'), patch.object(m.curses,'mousemask'), patch.object(m.curses,'mouseinterval'), patch.object(m.curses,'has_colors',return_value=False):
    for key in (m.curses.KEY_LEFT, m.curses.KEY_BACKSPACE):
        try:m.menu(Screen([key]),'Agents',['Muse'],True)
        except m.BackToProjects:pass
        else:raise AssertionError('Back did not work')
print('PASS: back navigation selects a different project; Left and Backspace return safely')

with patch.object(m,'menu',side_effect=[0,[0,1],m.BackToProjects(),[2,3],1]):
    project,team,leader=m.choose(None,[Path('/project')])
    assert leader=='codex' and team==[m.AGENTS[2],m.AGENTS[3]]
commands.clear()
with patch.object(m.subprocess,'run',side_effect=run):
    m.finish_setup({'base':['/mock/herdr','--session','test'],'agents':m.AGENTS,'panes':['w1:p'+str(i) for i in range(1,6)],'coordinator':'grok','bootstrap':'Read Bridge and claim'})
prompts=[x for x in commands if 'prompt' in x]
assert len(prompts)==1 and prompts[0][prompts[0].index('prompt')+1]=='grok'
print('PASS: coordinator back navigation; bootstrap sent only to chosen coordinator')
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    prompt=m.prepare_bridge(Path(tmp),'agy','test-session',m.AGENTS)
    assert 'claim --agent antigravity' in prompt
    assert (Path(tmp)/'.agent-bridge/state.sqlite3').exists()
    assert (Path(tmp)/'.agent-bridge/test-session.instructions.txt').exists()
print('PASS: real Bridge init, Antigravity mapping, saved coordinator instructions')
