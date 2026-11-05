import json,tempfile,unittest
from pathlib import Path
from router.store import Store
from router.profiles import load_profiles

class ProfileTests(unittest.TestCase):
 def make(self,root):
  (root/'dev').mkdir();(root/'dev'/'SOUL.md').write_text('Project {{PROJECT_NAME}}',encoding='utf-8')
  (root/'dev'/'AGENT.md').write_text('Implement within scope',encoding='utf-8')
  (root/'agents.json').write_text(json.dumps({'variables':{'PROJECT_NAME':'one'},'roles':{'developer':{'id':'FS_DEV','version':'v2','directory':'dev'}}}),encoding='utf-8')
 def test_load_and_task_snapshot(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);self.make(root);profiles=load_profiles(root)
   self.assertEqual(profiles['developer']['soul'],'Project one')
   s=Store(root/'db')
   try:
    s.add_project('one',root);s.update_project('one',agent_profiles=profiles);task=s.submit('one','goal')
    (root/'dev'/'AGENT.md').write_text('New rules',encoding='utf-8');new=load_profiles(root);s.update_project('one',agent_profiles=new)
    self.assertEqual(s.get(task['id'])['project']['agent_profiles'],profiles)
    self.assertNotEqual(new['developer']['digest'],profiles['developer']['digest'])
   finally:s.close()
 def test_reject_escape_unknown_variable_and_privilege_fields(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);self.make(root)
   (root/'dev'/'SOUL.md').write_text('{{MISSING}}',encoding='utf-8')
   with self.assertRaises(ValueError):load_profiles(root)
   (root/'dev'/'SOUL.md').write_text('safe',encoding='utf-8')
   manifest=json.loads((root/'agents.json').read_text());manifest['roles']['developer']['directory']='..'
   (root/'agents.json').write_text(json.dumps(manifest))
   with self.assertRaises(ValueError):load_profiles(root)
   manifest['roles']['developer']['directory']='dev';manifest['roles']['developer']['tools']='all'
   (root/'agents.json').write_text(json.dumps(manifest))
   with self.assertRaises(ValueError):load_profiles(root)

 def test_per_role_skills_filter_keeps_existing_denials(self):
  from unittest.mock import patch
  from subprocess import CompletedProcess
  from router.worker import role_skill_settings
  inventory = 'debug [Enabled] [Built-in]\nverify [Enabled] [Built-in]\nold [Disabled]\n'
  with patch('router.worker.subprocess.run', return_value=CompletedProcess([],0,stdout=inventory)):
   self.assertEqual(json.loads(role_skill_settings(['qodercli'],'.',{},['verify'],10))['skills']['disabled'], ['debug','old'])
   with self.assertRaisesRegex(ValueError,'Unavailable role skills'):
    role_skill_settings(['qodercli'],'.',{},['old'],10)
