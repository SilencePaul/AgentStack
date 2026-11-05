"""Real subprocess lifecycle with a simulated Qoder stream (no model/network)."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from router.store import Store
from router.service import Scheduler, Router


class ProcessTests(unittest.TestCase):
    def test_question_exit_restart_answer_and_four_role_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';repo.mkdir()
            subprocess.run(['git','init',str(repo)],capture_output=True,check=True)
            fake=root/'fake.py'
            fake.write_text('''import sys,json
from pathlib import Path
p=sys.stdin.read()
if 'ROLE: lead-plan' in p:out='ROUTER_LEAD: PLAN'
elif 'ROLE: developer' in p:
    if '"answer": "confirmed"' not in p:
        out='```router-receipt\\n'+json.dumps({'status':'needs_input','question':'Confirm?','completed':'Ready','remaining':'Write proof'})+'\\n```'
    else:
        Path('proof.txt').write_text('checked');out='Wrote proof'
elif 'ROLE: reviewer' in p:
    assert Path('proof.txt').read_text()=='checked';out='ROUTER_REVIEW: PASS'
elif 'ROLE: tester' in p:
    assert Path('proof.txt').read_text()=='checked';out='ROUTER_TEST: PASS'
else:out='All recorded stages returned'
print(json.dumps({'type':'result','result':out}))
''')
            db=root/'db';s=Store(db);s.add_project('p',str(repo),timeout=20,idle_timeout=5)
            route={'channel_id':'c','root_id':'thread','source_user_id':'u'}
            t=s.submit('p','Create proof\n```bridge-checks\n{"files":[{"path":"proof.txt","equals":"checked"}]}\n```',reply_to=route)
            scheduler=Scheduler(s,[sys.executable,str(fake)],root/'runs')
            def until(status):
                end=time.monotonic()+10
                while time.monotonic()<end:
                    scheduler.tick()
                    if s.get(t['id'])['status']==status:return
                    time.sleep(.02)
                self.fail(str(s.get(t['id'])))
            try:
                until('waiting_input');self.assertFalse(scheduler.workers)
                cp=s.get(t['id'])['continuation']
                self.assertFalse((repo/'proof.txt').exists())
                scheduler.shutdown();s.close();s=Store(db);self.assertEqual(s.recover(),0)
                Router(s).handle('/router answer '+t['id']+' '+cp['question_id']+' confirmed',reply_to=route)
                scheduler=Scheduler(s,[sys.executable,str(fake)],root/'runs');until('succeeded')
                self.assertEqual(s.get(t['id'])['attempt'],2)
                results=list((root/'runs').glob('*/attempt-*/*/result.json'))
                self.assertEqual(len(results),6)
                sessions=[json.loads(p.read_text(encoding='utf-8'))['session_id'] for p in results]
                self.assertEqual(len(set(sessions)),6)
                self.assertTrue(all(json.loads(p.read_text(encoding='utf-8'))['process_exit_code']==0 for p in results))
                self.assertEqual(len(s.outbox_pending()),2)  # question + final, no progress spam
            finally:scheduler.shutdown();s.close()
