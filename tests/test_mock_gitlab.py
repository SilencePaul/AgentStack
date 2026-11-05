import subprocess
import tempfile
import unittest
from pathlib import Path

from router.mock_gitlab import MockGitLab


class MockGitLabTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.service=MockGitLab(self.root/'server')
        self.addCleanup(self.service.close)
        self.repo=self.service.create_project('demo')
        self.work=self.root/'work'
        self.git('clone',str(self.repo),str(self.work))
        self.git('-C',str(self.work),'switch','-c','feature')
        (self.work/'feature.txt').write_text('feature\n')
        self.git('-C',str(self.work),'add','feature.txt')
        self.git('-C',str(self.work),'-c','user.name=Mock','-c','user.email=mock@localhost','commit','-m','feature')
        self.git('-C',str(self.work),'push','origin','feature')
        self.head=self.git('-C',str(self.work),'rev-parse','HEAD')
    def git(self,*args):
        result=subprocess.run(['git',*args],capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stderr)
        return result.stdout.strip()
    def test_verified_merge_updates_real_mock_branch_once(self):
        mr=self.service.open_mr('demo','feature','main')
        self.service.set_ci(mr['id'],self.head,'passed')
        self.service.record_check(mr['id'],self.head,mr['base_sha'],'review-task',True)
        merged=self.service.merge(mr['id'],self.head,mr['base_sha'])
        self.assertEqual(merged,self.service.merge(mr['id'],self.head,mr['base_sha']))
        self.assertEqual(self.git('--git-dir',str(self.repo),'show',merged+':feature.txt'),'feature')
        self.assertEqual(self.git('--git-dir',str(self.repo),'rev-parse','main'),merged)
    def test_failed_ci_or_changed_head_cannot_merge(self):
        mr=self.service.open_mr('demo','feature','main')
        self.service.record_check(mr['id'],self.head,mr['base_sha'],'review-task',True)
        self.service.set_ci(mr['id'],self.head,'failed')
        with self.assertRaises(ValueError):self.service.merge(mr['id'],self.head,mr['base_sha'])
        self.service.set_ci(mr['id'],self.head,'passed')
        (self.work/'feature.txt').write_text('changed\n')
        self.git('-C',str(self.work),'add','feature.txt')
        self.git('-C',str(self.work),'-c','user.name=Mock','-c','user.email=mock@localhost','commit','-m','changed')
        self.git('-C',str(self.work),'push','origin','feature')
        with self.assertRaises(ValueError):self.service.merge(mr['id'],self.head,mr['base_sha'])
        self.assertEqual(self.git('--git-dir',str(self.repo),'rev-parse','main'),mr['base_sha'])
    def test_project_names_cannot_escape_registry(self):
        for name in ('../outside','C:/private','a/b','--upload-pack=bad'):
            with self.assertRaises(ValueError):self.service.create_project(name)

    def test_inspect_reports_mergeability_without_approval_or_ref_changes(self):
        mr=self.service.open_mr('demo','feature','main')
        result=self.service.inspect(mr['id'])
        self.assertTrue(result['mergeable'])
        self.assertEqual(result['conflicts'],[])
        self.assertEqual(self.service.ref(self.repo,'main'),mr['base_sha'])
        self.assertEqual(self.service.get(mr['id'])['checks'],[])

    def test_conflict_preserves_target_then_resolved_version_can_merge(self):
        self.git('-C',str(self.work),'switch','main')
        (self.work/'feature.txt').write_text('target version\n')
        self.git('-C',str(self.work),'add','feature.txt')
        self.git('-C',str(self.work),'-c','user.name=Mock','-c','user.email=mock@localhost','commit','-m','target change')
        self.git('-C',str(self.work),'push','origin','main')
        mr=self.service.open_mr('demo','feature','main')
        inspection=self.service.inspect(mr['id'])
        self.assertFalse(inspection['mergeable'])
        self.assertIn('feature.txt',inspection['conflicts'])
        self.service.set_ci(mr['id'],self.head,'passed')
        self.service.record_check(mr['id'],self.head,mr['base_sha'],'conflicting-review',True)
        with self.assertRaisesRegex(ValueError,'conflict'):
            self.service.merge(mr['id'],self.head,mr['base_sha'])
        self.assertEqual(self.service.ref(self.repo,'main'),mr['base_sha'])
        self.git('-C',str(self.work),'switch','feature')
        self.git('-C',str(self.work),'-c','user.name=Mock','-c','user.email=mock@localhost','merge','-s','ours','main','-m','Resolve mock conflict keeping feature')
        self.git('-C',str(self.work),'push','origin','feature')
        updated=self.service.refresh(mr['id'])
        with self.assertRaises(ValueError):self.service.merge(mr['id'],self.head,mr['base_sha'])
        self.service.set_ci(mr['id'],updated['head_sha'],'passed')
        self.service.record_check(mr['id'],updated['head_sha'],updated['base_sha'],'resolved-review',True)
        merged=self.service.merge(mr['id'],updated['head_sha'],updated['base_sha'])
        self.assertEqual(self.git('--git-dir',str(self.repo),'show',merged+':feature.txt'),'feature')

    def test_restart_after_ref_update_finishes_without_second_merge(self):
        mr=self.service.open_mr('demo','feature','main')
        self.service.set_ci(mr['id'],self.head,'passed')
        self.service.record_check(mr['id'],self.head,mr['base_sha'],'review-task',True)
        original=self.service.save
        def fail_final_save(value):
            if value['state']=='merged':raise RuntimeError('simulated interruption')
            original(value)
        self.service.save=fail_final_save
        with self.assertRaisesRegex(RuntimeError,'simulated interruption'):
            self.service.merge(mr['id'],self.head,mr['base_sha'])
        committed=self.service.ref(self.repo,'main')
        restarted=MockGitLab(self.root/'server')
        try:
            self.assertEqual(restarted.merge(mr['id'],self.head,mr['base_sha']),committed)
            self.assertEqual(restarted.get(mr['id'])['state'],'merged')
            self.assertEqual(restarted.ref(self.repo,'main'),committed)
        finally:restarted.close()
