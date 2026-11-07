"""Herdr readiness checks: process presence is separate from a verified model reply."""
import concurrent.futures
import json
import re
import secrets
import shutil
import subprocess
import time


def check_session(session, probe=False):
    binary=shutil.which('herdr') or '/opt/homebrew/bin/herdr'
    base=[binary,'--session',session]
    def command(*args,timeout=8):
        return subprocess.run(base+list(args),capture_output=True,text=True,timeout=timeout)
    listed=command('agent','list')
    if listed.returncode:raise ValueError('Herdr session unavailable')
    agents=json.loads(listed.stdout)['result']['agents']
    kinds=('claude','codex','grok','muse','antigravity')
    def check(kind):
        actual='agy' if kind=='antigravity' else kind
        matches=[a for a in agents if a.get('agent')==actual]
        if not matches:return [{'agent':kind,'pane':'','status':'not_running','detail':'Agent is not running','checked':time.time()}]
        results=[]
        for agent in matches:
            pane=agent['pane_id'];state=agent.get('agent_status')
            status='unknown';detail='No model round-trip performed'
            try:
                if state=='working':status='busy';detail='Working; no diagnostic sent'
                elif state=='blocked':status='blocked';detail='Interactive dialog needs attention'
                elif probe and agent.get('interactive_ready'):
                    suffix=secrets.token_hex(8).upper();marker='BRIDGE_READY_'+suffix
                    # The complete marker never appears in the prompt, avoiding echo false positives.
                    prompt=('Readiness diagnostic only: do not use tools, modify files, delegate or take coordinator control. '
                            'Respond with the concatenation of the prefix BRIDGE_READY_ and the suffix '+suffix+', with no spaces or other text.')
                    submitted=command('agent','prompt',pane,prompt,timeout=10)
                    if submitted.returncode:status='blocked';detail='Submission rejected; inspect pane before retrying'
                    else:
                        deadline=time.monotonic()+35
                        status='unconfirmed';detail='No verified model reply; input may be pending'
                        while time.monotonic()<deadline:
                            output=command('agent','read',pane,'--source','visible','--lines','120').stdout
                            if marker in output:
                                status='ready';detail='Verified unique model reply';break
                            if re.search(r'usage limit reached|you.ve hit your limit|rate limit exceeded|quota exceeded',output,re.I):
                                status='quota_blocked';detail='Visible quota-limit message; inspect reset time in pane'
                                if re.search(r'continuing automatically|esc to cancel',output,re.I):
                                    command('agent','send-keys',pane,'esc')
                                break
                            time.sleep(.5)
                elif not agent.get('interactive_ready'):status='blocked';detail='Agent not ready for input'
                results.append({'agent':kind,'pane':pane,'status':status,'detail':detail,'checked':time.time()})
            except (OSError,ValueError,subprocess.TimeoutExpired):
                results.append({'agent':kind,'pane':pane,'status':'unconfirmed','detail':'Check failed; do not resend blindly','checked':time.time()})
        return results
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        return [item for group in pool.map(check,kinds) for item in group]
