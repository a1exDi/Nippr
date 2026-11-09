import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('launcher', 'herdr-launcher.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class RecentSessionsTests(unittest.TestCase):
    def test_discovery_keeps_running_and_stopped_and_bad_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'session.json').write_text(json.dumps({'workspaces':[
                {'custom_name':'Smart Fusion', 'identity_cwd':'/project'}]}))
            entries = [{'name':'active','running':True,'session_dir':tmp},
                       {'name':'old','running':False,'session_dir':tmp+'/missing'}]
            with patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps({'sessions':entries}))):
                sessions = m.recent_sessions('/herdr')
            self.assertEqual([s['name'] for s in sessions],['active','old'])
            self.assertIn('Smart Fusion · Работает',m.session_label(sessions[0]))
            self.assertIn('Остановлена',m.session_label(sessions[1]))

    def test_resume_does_not_create_or_bootstrap(self):
        class Attached(Exception): pass
        with patch.object(m,'projects',return_value=[]), patch.object(m,'recent_sessions',return_value=[{'name':'saved'}]), patch.object(m.shutil,'which',return_value='/herdr'), patch.object(m.curses,'wrapper',return_value='saved'), patch.object(m.os,'execv',side_effect=Attached) as attach, patch.object(m,'prepare_bridge') as bootstrap, patch.object(m.subprocess,'Popen') as start:
            with self.assertRaises(Attached): m.main()
            attach.assert_called_once_with('/herdr',['/herdr','session','attach','saved'])
            bootstrap.assert_not_called()
            start.assert_not_called()

    def test_picker_and_back_to_recent(self):
        sessions = [{'name':'saved','project':'Demo','updated':0,'running':True}]
        with patch.object(m,'menu',return_value=0):
            self.assertEqual(m.choose_launch(None,[],sessions),'saved')
        with patch.object(m,'menu',side_effect=[1,0]), patch.object(m,'choose',side_effect=m.BackToSessions):
            self.assertEqual(m.choose_launch(None,[],sessions),'saved')

    def test_failure_does_not_fall_back_to_new_session(self):
        with patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr='offline')):
            with self.assertRaises(RuntimeError): m.recent_sessions('/herdr')

if __name__ == '__main__': unittest.main()
