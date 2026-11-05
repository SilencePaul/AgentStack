import tempfile
import unittest
from pathlib import Path
from router.store import Store
from router.admin import TeamAdmin


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.ops=Store(self.root/'ops.db');self.business=Store(self.root/'business.db')
        self.admin=TeamAdmin(self.ops,self.business,self.root/'projects',self.root/'configs',lambda goal,catalog:{'kind':'team','id':'example','repository':'empty','rules':'Read only','roles':{}})
        self.route={'channel_id':'c','root_id':'r','source_user_id':'u'}
    def tearDown(self):
        self.ops.close();self.business.close();self.tmp.cleanup()
    def test_natural_draft_requires_bound_apply_and_snapshots_roles(self):
        draft=self.admin.enqueue('Create empty example team','post',self.route)
        self.admin.tick()
        self.assertEqual(self.business.projects(),[])
        with self.assertRaises(ValueError):self.admin.apply_request(draft,{**self.route,'source_user_id':'other'})
        self.admin.apply_request(draft,self.route);self.admin.tick()
        project=self.business.projects()[0]
        self.assertEqual(project['id'],'example')
        self.assertEqual(project['agent_profiles']['leader']['tools'],[])
        self.assertEqual(self.ops.meta('admin:'+draft)['state'],'applied')
        self.admin.apply_request(draft,self.route);self.admin.tick()
        self.assertEqual(len(self.business.projects()),1)
    def test_reject_unsafe_repository_and_unknown_skill(self):
        for spec in ({'kind':'team','id':'bad','repository':'../../private','rules':'','roles':{}},
                     {'kind':'team','id':'bad','repository':'empty','rules':'','roles':{'developer':{'skills':['missing']}}}):
            with self.assertRaises(ValueError):self.admin.validate(spec)
    def test_webhook_cannot_enable_merge_without_policy_implementation(self):
        with self.assertRaises(ValueError):
            self.admin.validate({'kind':'webhook','id':'hook','team':'missing','project_id':1,'actions':['merge'],'mode':'auto-merge'})

    def test_partial_role_update_keeps_identity_rules_and_tools(self):
        spec={'kind':'team','id':'example','repository':'empty','rules':'r','roles':{}}
        self.admin.apply(spec,self.route,'first')
        profiles=self.business.projects()[0]['agent_profiles']
        profiles['developer'].update(soul='custom soul',agent='custom rules',tools=['Read'],id='CUSTOM')
        self.business.update_project('example',agent_profiles=profiles)
        spec['roles']={'developer':{'skills':[]}}
        self.admin.apply(spec,self.route,'second')
        actual=self.business.projects()[0]['agent_profiles']['developer']
        for key in ('soul','agent','tools','id'):self.assertEqual(actual[key],profiles['developer'][key])
        self.assertEqual(actual['skills'],[])

    def test_stale_hook_revision_cannot_overwrite_current_rule(self):
        self.business.add_project('p',self.root)
        spec={'kind':'webhook','id':'mr','team':'p','project_id':42,'actions':['open'],'mode':'review'}
        self.admin.apply(spec,self.route,'v1')
        with self.assertRaises(ValueError):self.admin.apply(spec,self.route,'v2',expected_hook=None)
