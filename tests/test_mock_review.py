import json
import unittest
from router.store import Store
from router.mock_review import MockReview
from tests import test_mock_gitlab


class MockReviewTests(unittest.TestCase):
    git=test_mock_gitlab.MockGitLabTests.git
    def setUp(self):
        test_mock_gitlab.MockGitLabTests.setUp(self)
        self.store=Store(self.root/'business.db');self.addCleanup(self.store.close)
        self.store.add_project('team',self.work)
        project=self.store.projects()[0];project['repository']='mock://demo'
        self.store.db.execute('UPDATE projects SET config=?',(json.dumps(project),))
        self.review=MockReview(self.store,self.service)

    def test_event_binds_registered_repository_and_prepares_exact_head(self):
        mr=self.service.open_mr('demo','feature','main')
        event=self.service.event(mr['id'])
        binding=self.review.describe(event,'team')
        self.review.prepare(binding)
        self.assertEqual(self.git('-C',str(self.work),'rev-parse','HEAD'),mr['head_sha'])
        event['object_attributes']['last_commit']['id']='a'*40
        with self.assertRaises(ValueError):self.review.describe(event,'team')

    def test_dirty_workspace_and_unverified_task_cannot_be_approved(self):
        mr=self.service.open_mr('demo','feature','main')
        binding=self.review.describe(self.service.event(mr['id']),'team')
        (self.work/'feature.txt').write_text('uncommitted')
        with self.assertRaises(ValueError):self.review.prepare(binding)
        task=self.store.submit('team','review')
        self.store.set_meta('mock-review:'+task['id'],binding)
        with self.assertRaises(ValueError):self.review.check(mr['id'],task['id'],{})

    def test_receiver_persists_binding_and_deduplicates_before_checkout(self):
        from router.webhooks import GitLabWebhook
        mr=self.service.open_mr('demo','feature','main')
        self.store.set_meta('webhook:rule',{'team':'team','project_id':self.service.project_number('demo'),
                                          'revision':'one','actions':['update'],'route':{}})
        hook=GitLabWebhook(self.store,'test-secret',self.review)
        event=self.service.event(mr['id'])
        result=hook.receive('rule','test-secret','Merge Request Hook',event)
        self.assertEqual(self.store.meta('mock-review:'+result['task_id'])['head'],mr['head_sha'])
        duplicate=hook.receive('rule','test-secret','Merge Request Hook',event)
        self.assertEqual(duplicate['task_id'],result['task_id'])
        self.assertEqual(duplicate,result)
        self.assertEqual(len(self.store.tasks()),1)
        self.assertIn('target_head_sha',self.store.get(result['task_id'])['goal'])
        retry={**event,'mock_retry_task':result['task_id']}
        with self.assertRaises(ValueError):hook.receive('rule','test-secret','Merge Request Hook',retry)
        self.store.finish(result['task_id'],'failed','fixture failure')
        recovered=hook.receive('rule','test-secret','Merge Request Hook',retry)
        self.assertNotEqual(recovered['task_id'],result['task_id'])
        self.assertEqual(hook.receive('rule','test-secret','Merge Request Hook',retry),recovered)
        self.assertEqual(self.store.get(result['task_id'])['status'],'failed')
        self.assertEqual(len(self.store.tasks()),2)

    def test_check_requires_actual_role_evidence_and_unchanged_workspace(self):
        from router.continuity import workspace_stamp
        mr=self.service.open_mr('demo','feature','main')
        binding=self.review.describe(self.service.event(mr['id']),'team')
        self.review.prepare(binding)
        route={'channel_id':'c','source_user_id':'u'}
        task=self.store.submit('team','review',reply_to=route)
        self.store.set_meta('mock-review:'+task['id'],binding)
        evidence=self.root/'evidence';evidence.mkdir()
        (evidence/'final-snapshot.json').write_text(json.dumps({'git':workspace_stamp(self.work)}))
        (evidence/'role-reports.json').write_text('[]')
        self.store.finish(task['id'],'reported_unverified','fixture',str(evidence))
        with self.assertRaisesRegex(ValueError,'reviewer'):self.review.check(mr['id'],task['id'],route)
        reports=[{'role':role,'verdict':'PASS','process_exit_code':0} for role in ('reviewer','tester')]
        (evidence/'role-reports.json').write_text(json.dumps(reports))
        with self.assertRaisesRegex(ValueError,'owner'):self.review.check(mr['id'],task['id'],{'channel_id':'other'})
        result=self.review.check(mr['id'],task['id'],route)
        self.assertEqual(result['head'],mr['head_sha'])
        (self.work/'feature.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'Workspace changed'):self.review.check(mr['id'],task['id'],route)

    def test_duplicate_after_merge_returns_original_task_but_new_rule_cannot_admit_closed_mr(self):
        from router.webhooks import GitLabWebhook
        mr=self.service.open_mr('demo','feature','main')
        rule={'team':'team','project_id':self.service.project_number('demo'),'revision':'one','actions':['update'],'route':{}}
        self.store.set_meta('webhook:rule',rule)
        hook=GitLabWebhook(self.store,'test-secret',self.review)
        event=self.service.event(mr['id'])
        result=hook.receive('rule','test-secret','Merge Request Hook',event)
        self.service.set_ci(mr['id'],mr['head_sha'],'passed')
        self.service.record_check(mr['id'],mr['head_sha'],mr['base_sha'],'fixture',True)
        merged=self.service.merge(mr['id'],mr['head_sha'],mr['base_sha'])
        self.assertEqual(hook.receive('rule','test-secret','Merge Request Hook',event),result)
        self.assertEqual(self.service.event(mr['id']),event)
        self.assertEqual(self.service.ref(self.repo,'main'),merged)
        self.assertEqual(len(self.store.tasks()),1)
        with self.assertRaises(PermissionError):hook.receive('rule','wrong','Merge Request Hook',event)
        self.store.set_meta('webhook:rule',{**rule,'revision':'two'})
        with self.assertRaises(ValueError):hook.receive('rule','test-secret','Merge Request Hook',event)

    def test_duplicate_old_event_after_registry_refresh_does_not_checkout_again(self):
        from router.webhooks import GitLabWebhook
        mr=self.service.open_mr('demo','feature','main')
        self.store.set_meta('webhook:rule',{'team':'team','project_id':self.service.project_number('demo'),'revision':'one','actions':['update'],'route':{}})
        hook=GitLabWebhook(self.store,'test-secret',self.review)
        event=self.service.event(mr['id']);result=hook.receive('rule','test-secret','Merge Request Hook',event)
        tree=self.service.git(self.repo,'rev-parse',mr['head_sha']+'^{tree}').stdout.strip()
        head=self.service.git(self.repo,'commit-tree',tree,'-p',mr['head_sha'],'-m','new version').stdout.strip()
        self.service.git(self.repo,'update-ref','refs/heads/feature',head)
        self.service.refresh(mr['id'])
        self.assertEqual(hook.receive('rule','test-secret','Merge Request Hook',event),result)
        self.assertEqual(self.git('-C',str(self.work),'rev-parse','HEAD'),mr['head_sha'])
        self.assertEqual(len(self.store.tasks()),1)

    def test_new_target_version_is_not_hidden_by_delivery_cache(self):
        from router.webhooks import GitLabWebhook
        mr=self.service.open_mr('demo','feature','main')
        self.store.set_meta('webhook:rule',{'team':'team','project_id':self.service.project_number('demo'),'revision':'one','actions':['update'],'route':{}})
        hook=GitLabWebhook(self.store,'test-secret',self.review)
        old_event=self.service.event(mr['id']);first=hook.receive('rule','test-secret','Merge Request Hook',old_event)
        self.store.finish(first['task_id'],'reported_unverified','fixture')
        tree=self.service.git(self.repo,'rev-parse',mr['base_sha']+'^{tree}').stdout.strip()
        target=self.service.git(self.repo,'commit-tree',tree,'-p',mr['base_sha'],'-m','new target').stdout.strip()
        self.service.git(self.repo,'update-ref','refs/heads/main',target)
        event=self.service.event(mr['id'])
        self.assertNotEqual(event['mock_target_sha'],old_event['mock_target_sha'])
        second=hook.receive('rule','test-secret','Merge Request Hook',event)
        self.assertNotEqual(second['task_id'],first['task_id'])
        self.assertEqual(hook.receive('rule','test-secret','Merge Request Hook',old_event),first)
        self.assertEqual(len(self.store.tasks()),2)
