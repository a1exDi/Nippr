import sys
sys.path.insert(0,"usage-monitor")
import importlib.util
import json
import tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('monitor','usage-monitor/monitor.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp)/'rollout.jsonl'
 events=[{'type':'event_msg','timestamp':'2026-01-01T00:00:00Z','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':100,'output_tokens':20,'cached_input_tokens':50},'last_token_usage':{'total_tokens':60},'model_context_window':1000},'rate_limits':{'primary':{'used_percent':25,'window_minutes':10080,'resets_at':1794380767}}}}]
 p.write_text('\n'.join(json.dumps(e) for e in events)+'\n{partial')
 x=m.codex_record(p)
 assert x['input']==100 and x['context_observed']==60
 assert '75.0%' in m.quota_lines(x['limits'])[0]
 assert '7 дней' in m.quota_lines(x['limits'])[0]
 assert m.quota_lines({'primary':None})==[]
 assert m.fmt(None)=='—'
 c=m.claude_record({'context_window':{'used_percentage':0,'total_input_tokens':0},'rate_limits':{'five_hour':{'used_percentage':0}}})
 assert c['context_pct']==0 and '100.0%' in m.quota_lines(c['limits'])[0]
 assert 'источника' in m.details('Muse',None)[0]
print('PASS: real schemas, null/zero values, quota windows, incomplete JSONL, unsupported agents')

assert m.progress(0,10)=='░'*10+'  0.0%'
assert m.progress(100,10)=='█'*10+'  100.0%'
assert m.progress(50,10)=='█'*5+'░'*5+'  50.0%'
assert 'нет данных' in m.progress(None)
print('PASS: progress 0/50/100, unknown quota is not zero')
