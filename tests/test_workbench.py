import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from router.continuity import read_receipt, workspace_stamp
from router.public_events import public_events
from router.service import Scheduler
from router.store import Store
from router.workbench import make_server, events
from router.workbench_commands import command
from router.workspaces import prepare

open_url = build_opener(ProxyHandler({})).open


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root/'repo'
        self.repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True).strip()
        self.git = git
        git('init', '-q')
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@localhost')
        (self.repo/'README.md').write_text('baseline')
        git('add', '.')
        git('commit', '-qm', 'initial')
        self.store = Store(self.root/'state.sqlite3')
        self.addCleanup(self.store.close)
        self.store.add_project('app', self.repo)

    def request(self, **body):
        body.setdefault('request_id', 'request-' + str(time.time_ns()))
        return command(self.store, body)

    def test_parallel_workspaces_keep_dirty_source_and_resume_directory(self):
        self.store.update_project('app', workspace_mode='git', concurrency=2)
        a = self.store.submit('app', 'first')
        b = self.store.submit('app', 'second')
        c = self.store.submit('app', 'third')
        claimed = [self.store.claim(3), self.store.claim(3)]
        self.assertEqual({t['id'] for t in claimed}, {a['id'], b['id']})
        self.assertIsNone(self.store.claim(3))
        (self.repo/'README.md').write_text('uncommitted original')
        projects = [prepare(t, self.root/'runtime') for t in claimed]
        self.assertNotEqual(projects[0]['path'], projects[1]['path'])
        for task, project in zip(claimed, projects):
            self.store.save_workspace(task['id'], project)
        (Path(projects[0]['path'])/'README.md').write_text('only task A')
        self.assertEqual((Path(projects[1]['path'])/'README.md').read_text(), 'baseline')
        self.assertEqual((self.repo/'README.md').read_text(), 'uncommitted original')
        self.assertEqual(prepare(self.store.get(a['id']), self.root/'runtime')['path'], projects[0]['path'])
        checkpoint = {'question_id':'q','question':'Choose','answers':[], 'index':0}
        self.store.wait_input(a['id'], checkpoint, None)
        self.assertEqual(self.store.claim(3)['id'], c['id'])
        self.store.finish(b['id'], 'failed', 'failure confined to B')
        self.assertFalse(self.store.meta('project_paused:app', False))

    def test_legacy_serial_task_blocks_isolated_task(self):
        a = self.store.submit('app','legacy')
        self.store.claim()
        self.store.update_project('app', workspace_mode='git', concurrency=2)
        self.store.submit('app','new')
        self.assertIsNone(self.store.claim(4))
        self.store.wait_input(a['id'], {'question_id':'q','question':'ask','answers':[],'index':0}, None)
        self.assertIsNone(self.store.claim(4))

    def test_approval_and_command_receipts_are_idempotent_and_bound(self):
        body = {'action':'create','project_id':'app','goal':'read README','request_id':'create-000'}
        first = command(self.store, body)
        self.assertEqual(command(self.store, body), first)
        self.assertIsNone(self.store.claim())
        tid = first['task_id']
        with self.assertRaises(ValueError):
            self.request(action='approve',task_id=tid,goal_digest='stale',approved=True)
        body = {'action':'approve','task_id':tid,'goal_digest':hashlib.sha256(b'read README').hexdigest(),
                'approved':True,'request_id':'approve-000'}
        self.assertEqual(command(self.store, body), command(self.store, body))
        self.assertEqual(self.store.claim()['id'],tid)
        with self.assertRaises(ValueError):
            command(self.store,{**body,'approved':False})
        self.assertEqual(len(self.store.tasks()),1)

    def test_mm_question_answered_on_web_does_not_get_answered_twice(self):
        route = {'channel_id':'c','root_id':'r','source_user_id':'u','final_only':True}
        task = self.store.submit('app','read README',reply_to=route)
        self.store.claim()
        checkpoint = {'question_id':'q','question':'Choose','answers':[], 'index':0}
        self.store.wait_input(task['id'],checkpoint,None)
        self.request(action='answer',task_id=task['id'],question_id='q',answer='option A')
        with self.assertRaises(ValueError):
            self.store.answer(task['id'],'q','option B',route)
        answer = self.store.get(task['id'])['continuation']['answers']
        self.assertEqual([a['answer'] for a in answer],['option A'])
        self.assertEqual(len(self.store.outbox_pending()),0)

    def test_confirmation_rejects_stale_workspace_and_refusal_finishes(self):
        task = self.store.submit('app','work')
        self.store.claim()
        fence = chr(96)*3
        receipt = read_receipt(fence+'router-receipt\n'+json.dumps({
            'status':'needs_input','question':'Execute test?','completed':'read',
            'remaining':'test','input_kind':'confirmation'})+'\n'+fence)
        checkpoint = {**receipt,'question_id':'q','answers':[],'index':0,'workspace':workspace_stamp(self.repo)}
        self.store.wait_input(task['id'],checkpoint,None)
        (self.repo/'README.md').write_text('changed')
        with self.assertRaises(ValueError):
            self.request(action='answer',task_id=task['id'],question_id='q',approved=True)
        self.request(action='answer',task_id=task['id'],question_id='q',approved=False)
        self.assertEqual(self.store.get(task['id'])['status'],'cancelled')

    def test_public_events_strip_reasoning_redact_and_page(self):
        task = self.store.submit('app','work')
        values = public_events({'type':'assistant','message':{'content':[
            {'type':'thinking','thinking':'PRIVATE reasoning'},
            {'type':'text','text':'Reading file. token=secret'},
            {'type':'tool_use','name':'Write','input':{'file_path':'x.py','content':'PRIVATE file contents'}}
        ]}})
        self.assertNotIn('PRIVATE',str(values))
        self.assertNotIn('secret',str(values))
        for value in values:
            self.store.event(task['id'],'public_output',{'role':'developer','detail':value})
        batch = events(self.store,task['id'])
        self.assertEqual(len(batch['events']),2)
        self.assertEqual(events(self.store,task['id'],batch['cursor'])['events'],[])

    def test_http_requires_same_origin_token_and_persists_confirmation(self):
        server = make_server(self.root/'state.sqlite3',0)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        url = 'http://127.0.0.1:'+str(server.server_port)
        def post(body, headers):
            return open_url(Request(url+'/api/command',data=json.dumps(body).encode(),headers=headers))
        try:
            token = json.load(open_url(url+'/api/session'))['token']
            headers = {'Content-Type':'application/json','Origin':url,'X-Workbench-Token':token}
            body = {'action':'create','project_id':'app','goal':'safe task','request_id':'http-create'}
            for bad in ({}, {**headers,'Origin':'https://evil.example'}, {**headers,'X-Workbench-Token':'bad'}):
                with self.assertRaises(HTTPError) as caught:
                    post(body,bad)
                self.assertEqual(caught.exception.code,403)
                caught.exception.close()
            with post(body,headers) as response:
                result = json.load(response)
            self.assertEqual(self.store.get(result['task_id'])['status'],'awaiting_approval')
            request = Request(url+'/api/task?task='+result['task_id'],headers={'X-Workbench-Token':token})
            self.assertEqual(json.load(open_url(request))['goal'],'safe task')
            self.assertEqual(open_url(url).status,200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_scheduler_runs_two_isolated_tasks_and_stops_one(self):
        self.store.update_project('app', workspace_mode='git', concurrency=2)
        class Worker:
            def __init__(self, command, task, path, event):
                self.task = task
                self.stopped = False
            def start(self): pass
            def poll(self): return None
            def stop(self, reason): self.stopped = True
        a = self.store.submit('app','a')
        b = self.store.submit('app','b')
        scheduler = Scheduler(self.store,[],self.root/'runtime'/'runs',2,Worker)
        try:
            deadline = time.monotonic()+10
            while len(scheduler.workers)<2 and time.monotonic()<deadline:
                scheduler.tick()
                time.sleep(.02)
            self.assertEqual(len(scheduler.workers),2)
            self.request(action='stop',task_id=a['id'])
            scheduler.tick()
            self.assertEqual(self.store.get(a['id'])['status'],'cancelled')
            self.assertEqual(self.store.get(b['id'])['status'],'running')
        finally:
            scheduler.shutdown()
