import tempfile
import unittest
from pathlib import Path
from router.store import Store
from router.webhooks import GitLabWebhook


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.store=Store(root/'db');self.store.add_project('review-team',root)
        self.store.set_meta('webhook:mr',{'project_id':42,'team':'review-team','actions':['open','update'],'mode':'review','revision':'v1','route':{'channel_id':'c','root_id':'r','source_user_id':'u'}})
        self.hook=GitLabWebhook(self.store,'secret')
        self.event={'object_kind':'merge_request','project':{'id':42},'object_attributes':{'iid':7,'action':'open','title':'Review me','last_commit':{'id':'a'*40}}}
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def test_authenticated_event_routes_once_with_snapshot(self):
        one=self.hook.receive('mr','secret','Merge Request Hook',self.event)
        two=self.hook.receive('mr','secret','Merge Request Hook',self.event)
        self.assertEqual(one,two);self.assertEqual(len(self.store.tasks()),1)
        task=self.store.get(one['task_id'])
        self.assertEqual(task['reply_to']['channel_id'],'c')
        self.assertIn('不合并',task['goal'])
    def test_bad_secret_project_and_event_cannot_dispatch(self):
        with self.assertRaises(PermissionError):self.hook.receive('mr','bad','Merge Request Hook',self.event)
        self.event['project']['id']=43
        with self.assertRaises(ValueError):self.hook.receive('mr','secret','Merge Request Hook',self.event)
        self.assertEqual(self.store.tasks(),[])
    def test_new_commit_is_new_task_but_merge_event_is_ignored(self):
        self.hook.receive('mr','secret','Merge Request Hook',self.event)
        self.event['object_attributes'].update(action='update',last_commit={'id':'b'*40})
        self.hook.receive('mr','secret','Merge Request Hook',self.event)
        self.assertEqual(len(self.store.tasks()),2)
        self.event['object_attributes']['action']='merge'
        self.assertEqual(self.hook.receive('mr','secret','Merge Request Hook',self.event),{'ignored':True})
