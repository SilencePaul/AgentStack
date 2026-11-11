import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from router.pilot_setup import apply
from router.store import Store

class PilotSetupTests(unittest.TestCase):
    def test_preset_preserves_credentials_and_only_applies_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'router').mkdir()
            preset={'id':'test-v2','bot_id':'bot','user_id':'user','channel':{'channel_id':'channel','bot_username':'ag_test','project_id':'pilot'}}
            (root/'pilot-preset.json').write_text(json.dumps(preset))
            config={'qoder_command':['qoder'],'mm':{'bot_id':'bot','channels':['other'],'allowed_user_ids':['other-user'],'secret':'unchanged'}}
            path=root/'config.local.json';path.write_text(json.dumps(config))
            store=Store(root/'state.db')
            try:
                with patch('router.pilot_setup.__file__',str(root/'router/pilot_setup.py')):
                    apply(store,config,path)
                    saved=json.loads(path.read_text())
                    self.assertEqual(saved['mm']['secret'],'unchanged')
                    self.assertEqual(saved['mm']['channels'][0],'other')
                    self.assertEqual(saved['concurrency'],2)
                    project=store.projects()[0]
                    self.assertEqual((project['workspace_mode'],project['concurrency']),('copy',2))
                    store.update_project('pilot',concurrency=3)
                    apply(store,config,path)
                    self.assertEqual(store.projects()[0]['concurrency'],3)
                    self.assertTrue(path.with_name(path.name+'.before-pilot-20261111').exists())
            finally:store.close()
