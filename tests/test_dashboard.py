import tempfile,time,unittest
from pathlib import Path
from router.store import Store
from router.dashboard import dashboard
class DashboardTests(unittest.TestCase):
 def test_heartbeat_and_resolved_incidents(self):
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db');s.add_project('one',root);t=s.submit('one','PRIVATE GOAL')
   s.db.execute("UPDATE tasks SET status='running' WHERE id=?",(t['id'],))
   s.event(t['id'],'role_started',{'role':'developer'})
   s.event(t['id'],'started',{'role':'developer','detail':{'pid':123,'session_id':'session'}})
   s.set_meta('service_heartbeat',str(time.time()));d=dashboard(s.db)
   self.assertEqual(d['metrics']['running_workers'],1);self.assertEqual(d['metrics']['active_teams'],1)
   self.assertNotIn('PRIVATE GOAL',str(d))
   s.set_meta('service_heartbeat','0');d=dashboard(s.db)
   self.assertIsNone(d['metrics']['running_workers']);self.assertEqual(d['workers'][0]['state'],'unknown')
   s.finish(t['id'],'failed','failure');self.assertEqual(dashboard(s.db)['metrics']['attention'],1)
   s.set_meta('incident_resolved:'+t['id'],'fixed');self.assertEqual(dashboard(s.db)['metrics']['attention'],0);s.close()
 def test_empty_project(self):
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db');s.add_project('one',root)
   self.assertIsNone(dashboard(s.db)['teams'][0]['task']);s.close()

 def test_repeated_role_visits_and_failed_latest_worker(self):
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db');s.add_project('one',root);t=s.submit('one','goal')
   for role in ('developer','reviewer','developer','reviewer','tester'):
    s.event(t['id'],'role_started',{'role':role,'round':1})
    s.event(t['id'],'role_finished',{'role':role,'status':'failed' if role=='tester' else 'reported_unverified'})
   d=dashboard(s.db)
   self.assertEqual([x['role'] for x in d['teams'][0]['journey']],['developer','reviewer','developer','reviewer','tester'])
   self.assertEqual(next(w for w in d['workers'] if w['role']=='developer')['visits'],2)
   self.assertEqual(next(w for w in d['workers'] if w['role']=='tester')['state'],'blocked');s.close()

 def test_running_team_is_not_hidden_by_new_queue_entry(self):
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db');s.add_project('one',root);a=s.submit('one','active')
   s.db.execute("UPDATE tasks SET status='running' WHERE id=?",(a['id'],));s.submit('one','later')
   self.assertEqual(dashboard(s.db)['teams'][0]['task']['id'],a['id']);s.close()

 def test_activity_privacy_tool_return_and_output_age(self):
  import json
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db');s.add_project('one',root);t=s.submit('one','PRIVATE_GOAL_SENTINEL')
   s.db.execute("UPDATE tasks SET status='running' WHERE id=?",(t['id'],))
   s.event(t['id'],'role_started',{'role':'developer','evidence':'PRIVATE_PATH_SENTINEL'})
   s.event(t['id'],'started',{'role':'developer','detail':{'pid':123,'session_id':'session'}})
   s.event(t['id'],'tool_activity',{'role':'developer','detail':{'tool':'Read','tool_id':'id-1','state':'started','input':'PRIVATE_ARGUMENT_SENTINEL'}})
   s.event(t['id'],'output',{'role':'developer','detail':{'text':'PRIVATE_OUTPUT_SENTINEL','thinking':'PRIVATE_THINKING_SENTINEL'}})
   s.event(t['id'],'tool_activity',{'role':'developer','detail':{'tool_id':'id-1','state':'returned','content':'PRIVATE_RESULT_SENTINEL'}})
   s.db.execute("UPDATE events SET created_at='2026-01-01 00:00:00' WHERE task_id=?",(t['id'],))
   s.set_meta('service_heartbeat','1767225610')
   with patch('router.dashboard.time.time',return_value=1767225610):d=dashboard(s.db)
   worker=d['workers'][0]
   self.assertEqual(worker['tool'],'Read');self.assertEqual(worker['tool_state'],'returned')
   self.assertEqual(worker['output_age_seconds'],10)
   self.assertEqual(worker['last_output'],'2026-01-01 00:00:00')
   self.assertEqual(worker['started_at'],'2026-01-01 00:00:00')
   self.assertEqual(d['activity'][-1]['tool'],'Read')
   self.assertNotIn('PRIVATE_',json.dumps(d));s.close()

 def test_retry_and_session_boundaries_do_not_reuse_tool_ids(self):
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db');s.add_project('one',root);t=s.submit('one','goal')
   s.event(t['id'],'role_started',{'role':'tester'})
   s.event(t['id'],'tool_activity',{'role':'tester','detail':{'tool':'Bash','tool_id':'reused','state':'started'}})
   s.event(t['id'],'role_retry_requested',{'role':'tester','reason':'PRIVATE_REASON_SENTINEL'})
   s.event(t['id'],'tool_activity',{'role':'tester','detail':{'tool_id':'reused','state':'error'}})
   self.assertIsNone(dashboard(s.db)['activity'][-1]['tool'])
   s.event(t['id'],'tool_activity',{'role':'tester','detail':{'tool':'Read','tool_id':'reused','state':'started'}})
   s.event(t['id'],'started',{'role':'tester','detail':{'session_id':'new-session'}})
   s.event(t['id'],'tool_activity',{'role':'tester','detail':{'tool_id':'reused','state':'returned'}})
   d=dashboard(s.db);self.assertIsNone(d['workers'][0]['tool'])
   self.assertNotIn('PRIVATE_REASON_SENTINEL',str(d));s.close()

 def test_safe_status_activity_and_eighty_item_limit(self):
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db');s.add_project('one',root);t=s.submit('one','goal')
   s.event(t['id'],'role_started',{'role':'developer'})
   s.event(t['id'],'tool_activity',{'role':'developer','detail':{'tool':'Write','tool_id':'long','state':'started'}})
   for i in range(100):s.event(t['id'],'role_verdict',{'role':'developer','verdict':'PASS','thinking':'PRIVATE_SENTINEL'})
   s.event(t['id'],'tool_activity',{'role':'developer','detail':{'tool_id':'long','state':'returned'}})
   s.event(t['id'],'waiting_input',{'question':'PRIVATE_QUESTION_SENTINEL'})
   s.event(t['id'],'answer_recorded',{'answer':'PRIVATE_ANSWER_SENTINEL'})
   d=dashboard(s.db)
   self.assertEqual(len(d['activity']),80)
   self.assertEqual(d['activity'][-3]['tool'],'Write')
   self.assertEqual([a['state'] for a in d['activity'][-2:]],['awaiting_user','answered'])
   self.assertNotIn('PRIVATE_',str(d));s.close()

 def test_resume_queue_explains_all_blockers_without_exposing_answer(self):
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db');s.add_project('one',root);t=s.submit('one','goal')
   s.db.execute("UPDATE tasks SET continuation=? WHERE id=?",('{"answers":[{"answer":"PRIVATE_ANSWER"}]}',t['id']))
   s.set_meta('service_heartbeat','stopped');s.set_meta('project_paused:one',True)
   d=dashboard(s.db);task=d['tasks'][0]
   self.assertTrue(task['resume_pending'])
   self.assertEqual(task['queue_blockers'],['router_offline','project_paused'])
   self.assertNotIn('PRIVATE_ANSWER',str(d))
   self.assertEqual([r['role'] for r in d['teams'][0]['roster']],['developer','reviewer','tester','lead-report'])
   self.assertTrue(all(r['state']=='not_started' for r in d['teams'][0]['roster']))
   s.set_meta('service_heartbeat',str(time.time()));s.set_meta('project_paused:one',False)
   self.assertEqual(dashboard(s.db)['tasks'][0]['queue_blockers'],[]);s.close()

 def test_failure_categories_and_queue_dependencies(self):
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db');s.add_project('one',root);a=s.submit('one','first');b=s.submit('one','second')
   s.db.execute("UPDATE tasks SET status='waiting_input' WHERE id=?",(a['id'],))
   s.set_meta('service_heartbeat',str(time.time()))
   d=dashboard(s.db)
   self.assertEqual(d['tasks'][0]['queue_blockers'],['project_busy'])
   s.db.execute("UPDATE tasks SET status='failed',summary='Workspace changed since checkpoint; PRIVATE' WHERE id=?",(a['id'],))
   d=dashboard(s.db);issue=next(i for i in d['issues'] if i['task_id']==a['id'])
   self.assertEqual(issue['category'],'checkpoint_changed');self.assertNotIn('PRIVATE',str(d));s.close()

 def test_leader_phases_are_one_member_with_real_handoffs(self):
  with tempfile.TemporaryDirectory() as root:
   s=Store(Path(root)/'db')
   try:
    s.add_project('one',root);t=s.submit('one','goal')
    for role in ('lead-plan','developer','reviewer','lead-triage','developer','lead-report'):
     s.event(t['id'],'role_started',{'role':role})
     s.event(t['id'],'role_finished',{'role':role,'status':'reported_unverified'})
    d=dashboard(s.db);leaders=[w for w in d['workers'] if w['role']=='lead-report']
    self.assertEqual(len(leaders),1);self.assertEqual(leaders[0]['visits'],3)
    self.assertEqual([j['phase'] for j in d['teams'][0]['journey']],['lead-plan','developer','reviewer','lead-triage','developer','lead-report'])
   finally:s.close()
