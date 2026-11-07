"""Read-only quota adapters. Credentials remain inside CLI or in memory."""
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time
import urllib.request


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def cli(name):
    path=shutil.which(name)
    if path: return path
    for root in (Path.home()/'.local/bin',Path.home()/'.grok/bin',Path('/opt/homebrew/bin')):
        if (root/name).is_file(): return str(root/name)
    raise RuntimeError('CLI не найден: '+name)


def run_json(name,args,timeout=30,env=None,cwd=None):
    result=subprocess.run([cli(name)]+args,capture_output=True,text=True,timeout=timeout,env=env,cwd=cwd)
    if result.returncode: raise RuntimeError('CLI не вернул данные; проверь вход через '+name)
    return json.loads(result.stdout)


def iso_epoch(value):
    try:return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (TypeError,ValueError,AttributeError):return None


def muse():
    data=run_json('muse',['usage','--json'])
    a=data.get('allowance') or {}
    if not a:raise RuntimeError('Muse: allowance отсутствует; выполни muse login')
    limits={}
    for key,target in [('window','five_hour'),('weekly','seven_day')]:
        item=a.get(key) or {}
        if isinstance(item.get('usedPercent'),(int,float)):
            limits[target]={'used_percentage':item['usedPercent'],'resets_at':item.get('resetsAtMs',0)/1000 or None}
    return {'plan':a.get('tier'),'limits':limits,'source':'muse usage --json','updated':now()}


def grok():
    path=Path.home()/'.grok/auth.json'
    data=json.loads(path.read_text())
    accounts=[v for v in data.values() if isinstance(v,dict) and v.get('key')]
    if len(accounts)!=1:raise RuntimeError('Grok: требуется ровно одна авторизация; проверь grok login')
    headers={'Authorization':'Bearer '+accounts[0]['key'],'x-xai-token-auth':'xai-grok-cli','Accept':'application/json'}
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(NoRedirect)
    def get(route):
        request=urllib.request.Request('https://cli-chat-proxy.grok.com/v1/'+route,headers=headers)
        with opener.open(request,timeout=15) as response:return json.load(response)
    data=get('billing?format=credits').get('config') or {}
    used=data.get('creditUsagePercent')
    if not isinstance(used,(int,float)):raise RuntimeError('Grok: сервис не сообщил процент квоты')
    period=data.get('currentPeriod') or {}
    label={'USAGE_PERIOD_TYPE_WEEKLY':'7 дней','USAGE_PERIOD_TYPE_MONTHLY':'Месяц'}.get(period.get('type'),'Кредиты')
    reset=iso_epoch(period.get('end') or data.get('billingPeriodEnd'))
    try:plan=get('settings').get('subscription_tier_display')
    except Exception:plan=None
    return {'plan':plan,'limits':{},'windows':[{'label':label,'used':used,'resets_at':reset}],
            'source':'Grok CLI billing API','updated':now()}


def antigravity():
    env=os.environ.copy();env['AGY_CLI_DISABLE_AUTO_UPDATE']='true'
    with tempfile.TemporaryDirectory(prefix='herdr-quota-') as tmp:
        # Quota queries must never open a browser login in the background.
        for name in ('open','xdg-open'):
            p=Path(tmp)/name;p.write_text('#!/bin/sh\nexit 1\n');p.chmod(0o700)
        env['PATH']=tmp+os.pathsep+env.get('PATH','');env['BROWSER']=str(Path(tmp)/'open')
        data=run_json('agy',['-p','/usage','--output-format','json'],timeout=90,env=env,cwd=tmp)
    if data.get('status')!='SUCCESS' or (data.get('command') or {}).get('name')!='usage':
        raise RuntimeError('Antigravity: отчёт usage недоступен; открой agy и войди')
    groups=(data['command'].get('data') or {}).get('groups') or []
    windows=[]
    for group in groups:
        for bucket in group.get('buckets') or []:
            remaining=bucket.get('remaining_fraction')
            if not isinstance(remaining,(int,float)) or not 0<=remaining<=1:continue
            group_name={'Gemini Models':'Gemini','Claude and GPT models':'Claude и GPT'}.get(group.get('name'),group.get('name','Модели'))
            label='Antigravity: '+group_name+' / '+{'weekly':'7 дней','5h':'5 часов'}.get(bucket.get('window'),bucket.get('name','Квота'))
            windows.append({'label':label,'used':100*(1-remaining),'resets_at':iso_epoch(bucket.get('reset_time'))})
    if not windows:raise RuntimeError('Antigravity: нет измеренных квот')
    return {'plan':None,'windows':windows,'source':'agy /usage (без модельного запроса)','updated':now()}


def claude():
    data=run_json('claude',['auth','status','--json'])
    if not data.get('loggedIn'):raise RuntimeError('Claude: требуется claude auth login')
    return {'plan':data.get('planTier') or data.get('subscriptionType'),'plan_source':'claude auth status'}


def codex():
    p=subprocess.Popen([cli('codex'),'app-server'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
    sel=selectors.DefaultSelector();sel.register(p.stdout,selectors.EVENT_READ)
    buf=b''
    def rpc(i,method,params):
        nonlocal buf
        p.stdin.write((json.dumps({'id':i,'method':method,'params':params})+'\n').encode());p.stdin.flush()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            while b'\n' in buf:
                line,buf=buf.split(b'\n',1)
                try:answer=json.loads(line)
                except ValueError:continue
                if answer.get('id')==i:
                    if 'error' in answer:raise RuntimeError('Codex RPC: метод недоступен')
                    return answer.get('result') or {}
            if sel.select(.5):
                part=os.read(p.stdout.fileno(),65536)
                if not part:raise RuntimeError('Codex RPC завершился')
                buf+=part
        raise RuntimeError('Codex RPC: timeout')
    try:
        rpc(1,'initialize',{'clientInfo':{'name':'herdr_usage','version':'0.2.0'}})
        p.stdin.write(b'{"method":"initialized","params":{}}\n');p.stdin.flush()
        account=rpc(2,'account/read',{}).get('account') or {}
        result=rpc(3,'account/rateLimits/read',{})
        limit=result.get('rateLimits') or {}
        limits={}
        for key in ('primary','secondary'):
            item=limit.get(key)
            if isinstance(item,dict):limits[key]={'used_percent':item.get('usedPercent'),'window_minutes':item.get('windowDurationMins'),'resets_at':item.get('resetsAt')}
        return {'plan':account.get('planType'),'limits':limits,'source':'Codex App Server — account/rateLimits/read','updated':now()}
    finally:
        sel.close()
        p.terminate()
        try:p.wait(timeout=3)
        except subprocess.TimeoutExpired:p.kill();p.wait()


def collect():
    result={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        jobs={pool.submit(fn):name for name,fn in [('Codex',codex),('Claude',claude),('Muse',muse),('Grok',grok),('Antigravity',antigravity)]}
        for future in concurrent.futures.as_completed(jobs):
            name=jobs[future]
            try:result[name]=future.result()
            except Exception as error:
                # Never expose raw HTTP/CLI diagnostics that may include account identifiers.
                result[name]={'error':f'Источник недоступен ({type(error).__name__}); проверь вход в CLI','attempted_at':now()}
    return result


if __name__=='__main__':
    print(json.dumps(collect(),ensure_ascii=False,indent=2))
