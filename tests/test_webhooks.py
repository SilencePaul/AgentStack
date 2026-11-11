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

    def test_receiver_restart_preserves_http_address_and_event_deduplication(self):
        import json
        from urllib.request import Request,urlopen
        from router.webhooks import WebhookListener
        listener=WebhookListener(('127.0.0.1',0),self.hook)
        try:
            listener.start();address=listener.address
            def post():
                request=Request('http://127.0.0.1:'+str(address[1])+'/hooks/gitlab/mr',
                                data=json.dumps(self.event).encode(),
                                headers={'X-Gitlab-Token':'secret','X-Gitlab-Event':'Merge Request Hook'})
                with urlopen(request,timeout=5) as response:return json.load(response)
            original=post()
            result=listener.restart()
            self.assertEqual(result['generation'],2)
            self.assertEqual(listener.address,address)
            self.assertEqual(post(),original)
            self.assertEqual(len(self.store.tasks()),1)
        finally:listener.stop()
    def test_new_commit_is_new_task_but_merge_event_is_ignored(self):
        self.hook.receive('mr','secret','Merge Request Hook',self.event)
        self.event['object_attributes'].update(action='update',last_commit={'id':'b'*40})
        self.hook.receive('mr','secret','Merge Request Hook',self.event)
        self.assertEqual(len(self.store.tasks()),2)
        self.event['object_attributes']['action']='merge'
        self.assertEqual(self.hook.receive('mr','secret','Merge Request Hook',self.event),{'ignored':True})

    def test_app_routes_author_and_emits_each_result_once(self):
        hook = GitLabWebhook(self.store, 'secret', app_rules={'mr': {
            'aliases': ['d365-nano'], 'path': 'sales/d365-nano', 'suite': ['pytest', 'tests/sales'],
        }}, authors={'81': 'author-mm'})
        self.event['user'] = {'id': 99}
        attrs = self.event['object_attributes']
        attrs.update(title='[d365-nano-other] fix', author_id=81)
        def receive():
            return hook.receive('mr', 'secret', 'Merge Request Hook', self.event)
        self.assertEqual(receive(), {'ignored': True})
        attrs.update(title='[d365-nano] fix')
        tid = receive()['task_id']
        self.assertEqual(receive()['task_id'], tid)
        self.assertEqual(self.store.outbox_pending(), [])
        task = self.store.get(tid)
        self.assertTrue(task['project']['review_only'])
        self.assertIn('compare_app_tests.py', task['goal'])
        self.store.finish(tid, 'reported_unverified', '已有失败 1，新增失败 0')
        attrs.update(action='merge', state='merged', target_branch='dev')
        receive()
        receive()
        notices = self.store.outbox_pending()
        self.assertEqual(len(notices), 2)
        self.assertIn('新增失败 0', notices[0]['text'])
        self.assertEqual(notices[1]['text'], 'MR !7 已合并到 dev。')
        for notice in notices:
            self.assertEqual(notice['reply_to']['mention_id'], 'author-mm')
