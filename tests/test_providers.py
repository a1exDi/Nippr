import sys
sys.path.insert(0,'usage-monitor')
import providers as p
from unittest.mock import patch
with patch.object(p,'run_json',return_value={'allowance':{'tier':'Everyday','window':{'usedPercent':0,'resetsAtMs':2000},'weekly':{'usedPercent':100}}}):
 r=p.muse();assert r['limits']['five_hour']['used_percentage']==0
 assert r['limits']['five_hour']['resets_at']==2
 assert r['limits']['seven_day']['used_percentage']==100
sample={'status':'SUCCESS','command':{'name':'usage','data':{'groups':[{'name':'A','buckets':[{'window':'weekly','remaining_fraction':.25,'reset_time':'2026-11-07T00:00:00Z'},{'window':'5h','remaining_fraction':1}]}]}}}
with patch.object(p,'run_json',return_value=sample):
 r=p.antigravity();assert [x['used'] for x in r['windows']]==[75,0]
with patch.object(p,'run_json',return_value={'status':'SUCCESS','command':{'name':'other'}}):
 try:p.antigravity()
 except RuntimeError:pass
 else:raise AssertionError('Invalid command accepted')
assert p.iso_epoch(None) is None
print('PASS: Muse zero/full values, reset units, Antigravity groups and inverse percentages, rejects unrelated reports')
