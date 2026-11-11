"""Real subprocess checks and negative tests for evidence-bound completion."""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0, 'universal-bridge')
import bridge as b


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.call('init', '--goal', 'Evidence gate test')
        self.coordinator = self.claim('codex')
        self.task = self.call('task-add', '--title', 'Write data', '--assignee', 'claude', token=self.coordinator)['id']
        self.worker = self.call('task-grant', self.task, '--resources', 'output', token=self.coordinator)['worker_token']
        (self.root/'output').mkdir()
        (self.root/'output/data.txt').write_text('verified content')
        self.plan([sys.executable, '-c', "from pathlib import Path; assert Path('output/data.txt').read_text() == 'verified content'; print('PASS: artifact content')"])

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, *args, token=None):
        prefix = ['--project', str(self.root)] + (['--token', token] if token else [])
        return b.execute(b.parser().parse_args(prefix+list(args)))

    def claim(self, agent):
        token = self.call('claim', '--agent', agent)['token']
        self.call('reconcile', '--revision', str(self.call('status')['revision']), '--evidence', 'Inspected fixture', token=token)
        return token

    def plan(self, argv, timeout=60):
        self.call('task-check', self.task, '--command-json', json.dumps(argv), '--timeout', str(timeout), token=self.coordinator)

    def review(self):
        return self.call('worker-report', self.task, '--status', 'review', '--artifact', 'output/data.txt', '--result', 'Proposal', '--next', 'Independent check', token=self.worker)

    def verify(self):
        return self.call('task-verify', self.task, token=self.coordinator)

    def accept(self, verification):
        return self.call('task-accept', self.task, '--verification', verification, '--note', 'Inspected the actual artifact and check output', token=self.coordinator)

    def done(self):
        return self.call('task-update', self.task, '--status', 'done', '--result', 'Accepted', token=self.coordinator)

    def test_text_and_direct_status_bypasses_rejected(self):
        with self.assertRaises(ValueError):
            self.call('worker-report', self.task, '--status', 'review', '--result', 'All tests passed!', '--next', 'Done', token=self.worker)
        args = b.parser().parse_args(['--project',str(self.root),'--token',self.worker,'worker-report',self.task,'--status','running','--result','done','--next','none'])
        args.status = 'done'  # bypass argparse, exercise the backend
        with self.assertRaises(ValueError): b.execute(args)
        with self.assertRaises(ValueError): self.done()
        with self.assertRaises(ValueError):
            self.call('task-update', self.task, '--status', 'review', '--result', 'I checked it', token=self.coordinator)
        self.assertEqual(self.call('status')['tasks'][0]['status'], 'running')

    def test_full_workflow_and_receipt(self):
        self.review()
        with self.assertRaises(ValueError): self.done()
        checked = self.verify()
        self.assertEqual(checked['status'], 'passed')
        self.assertEqual(checked['receipt']['exit_code'], 0)
        self.assertIn('PASS: artifact content', Path(checked['receipt']['log']).read_text())
        self.assertEqual(Path(checked['receipt']['log']).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError): self.done()
        self.accept(checked['verification'])
        self.done()
        state = self.call('status')
        self.assertEqual(state['tasks'][0]['status'], 'done')
        self.assertEqual(state['tasks'][0]['evidence']['acceptance']['agent'], 'codex')
        self.assertNotIn(self.worker, str(state))
        self.assertNotIn(self.coordinator, str(state))

    def test_worker_cannot_verify_pin_or_accept(self):
        self.review()
        for command in [('task-verify',self.task), ('task-check',self.task,'--command-json','["true"]'), ('task-accept',self.task,'--verification','fake','--note','Passed')]:
            with self.assertRaises(ValueError): self.call(*command, token=self.worker)

    def test_failed_check_and_timeout_and_missing_executable(self):
        self.review()
        for argv, timeout, status in [([sys.executable,'-c','print("FAIL"); raise SystemExit(3)'],60,'failed'), ([sys.executable,'-c','import time; time.sleep(20)'],1,'timeout'), (['/definitely/missing/nippr-check'],60,'failed')]:
            self.plan(argv, timeout)
            checked = self.verify()
            self.assertEqual(checked['status'], status)
            with self.assertRaises(ValueError): self.accept(checked['verification'])
            with self.assertRaises(ValueError): self.done()

    def test_changed_artifact_before_and_after_acceptance(self):
        self.review()
        checked = self.verify()
        self.accept(checked['verification'])
        (self.root/'output/data.txt').write_text('Changed after acceptance')
        with self.assertRaises(ValueError): self.done()
        with self.assertRaises(ValueError): self.accept(checked['verification'])
        with self.assertRaises(ValueError): self.verify()

    def test_resubmission_and_plan_change_invalidate(self):
        self.review()
        old = self.verify()
        self.accept(old['verification'])
        self.review()  # even identical bytes get a new submission revision
        with self.assertRaises(ValueError): self.done()
        with self.assertRaises(ValueError): self.accept(old['verification'])
        new = self.verify()
        self.accept(new['verification'])
        self.plan([sys.executable,'-c','print("New acceptance criteria")'])
        with self.assertRaises(ValueError): self.done()
        with self.assertRaises(ValueError): self.accept(new['verification'])

    def test_log_tampering_and_wrong_task_receipt(self):
        self.review()
        checked = self.verify()
        other = self.call('task-add','--title','Other','--assignee','grok',token=self.coordinator)['id']
        with self.assertRaises(ValueError):
            self.call('task-accept',other,'--verification',checked['verification'],'--note','Reuse',token=self.coordinator)
        self.accept(checked['verification'])
        Path(checked['receipt']['log']).write_text('Forged PASS')
        with self.assertRaises(ValueError): self.done()
        with self.assertRaises(ValueError): self.accept(checked['verification'])

    def test_paths_empty_files_and_scope(self):
        (self.root/'empty').touch()
        (self.root/'other.txt').write_text('Outside assignment')
        (self.root/'output/link.txt').symlink_to(self.root/'other.txt')
        for path in ['missing.txt','empty','../outside',str(self.root/'other.txt'),'other.txt','output/link.txt','.agent-bridge/state.sqlite3']:
            with self.assertRaises(ValueError):
                self.call('worker-report',self.task,'--status','review','--artifact',path,'--result','Ready','--next','Verify',token=self.worker)

    def test_changed_during_check_is_stale_and_no_write_lock(self):
        self.review()
        self.plan([sys.executable,'-c',"from pathlib import Path; import time; Path('started').write_text('yes'); time.sleep(.4)"])
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(self.verify)
            deadline = time.time()+5
            while not (self.root/'started').exists() and time.time()<deadline: time.sleep(.01)
            self.assertTrue((self.root/'started').exists())
            self.call('heartbeat',token=self.coordinator)  # would deadlock if verifier held the writer lock
            (self.root/'output/data.txt').write_text('Changed during check')
            self.assertEqual(future.result(timeout=5)['status'], 'stale')

    def test_handoff_requires_new_coordinator_acceptance(self):
        self.review()
        checked = self.verify()
        self.accept(checked['verification'])
        self.call('handoff','--to','grok','--summary','Verification passed','--next','Inspect evidence',token=self.coordinator)
        self.coordinator = self.claim('grok')
        with self.assertRaises(ValueError): self.done()
        self.accept(checked['verification'])
        self.done()

    def test_report_wrapper_forwards_artifacts_without_printing_capability(self):
        folder = self.root/'.agent-bridge/assignments'
        folder.mkdir()
        (folder/(self.task+'.json')).write_text(json.dumps({'id':self.task,'project':str(self.root),'worker_token':self.worker}))
        result = self.call('report',self.task,'--status','review','--artifact','output/data.txt','--result','Proposal','--next','Validate')
        self.assertNotIn(self.worker,str(result))
        self.assertEqual(len(self.call('status')['tasks'][0]['evidence']['artifacts']),1)

    def test_legacy_done_is_not_fabricated_acceptance(self):
        with b.database(self.root) as db:
            db.execute("UPDATE tasks SET status='done' WHERE id=?",(self.task,))
        evidence = self.call('status')['tasks'][0]['evidence']
        self.assertIsNone(evidence['acceptance'])
        self.assertIsNone(evidence['verification'])
        with self.assertRaises(ValueError): self.done()

    def test_queued_done_and_missing_plan(self):
        other = self.call('task-add','--title','Queued','--assignee','muse',token=self.coordinator)['id']
        with self.assertRaises(ValueError):
            self.call('task-update',other,'--status','done','--result','Trust me',token=self.coordinator)
        self.review()
        with b.database(self.root) as db: db.execute('DELETE FROM task_checks WHERE task=?',(self.task,))
        with self.assertRaises(ValueError): self.verify()


if __name__ == '__main__': unittest.main()
