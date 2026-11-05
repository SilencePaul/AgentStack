"""Bind mock events and actual team evidence to immutable Git versions."""
import json
from pathlib import Path
import subprocess

from .continuity import workspace_stamp


class MockReview:
    def __init__(self, store, mock):self.store,self.mock=store,mock

    @staticmethod
    def git(path,*args):
        result=subprocess.run(['git','-C',str(path),*args],capture_output=True,text=True,encoding='utf-8',timeout=30)
        if result.returncode:raise ValueError('Mock checkout operation failed: '+result.stderr[:300])
        return result.stdout.strip()

    def describe(self,event,team):
        mr=self.mock.get(event.get('mock_mr_id'))
        if mr['state']!='opened':raise ValueError('Mock MR is not open')
        attrs=event.get('object_attributes',{})
        expected={'source_branch':mr['source'],'target_branch':mr['target'],'iid':int(mr['id'][:8],16)+1}
        if (any(attrs.get(k)!=v for k,v in expected.items())
                or attrs.get('last_commit',{}).get('id')!=mr['head_sha']
                or event.get('mock_target_sha')!=mr['base_sha']
                or event.get('project',{}).get('id')!=self.mock.project_number(mr['project'])):
            raise ValueError('Mock event does not match registered MR version')
        project=next((p for p in self.store.projects() if p['id']==team),None)
        if not project or project.get('repository')!='mock://'+mr['project']:
            raise ValueError('Mock event repository differs from team binding')
        return {'mr_id':mr['id'],'team':team,'path':project['path'],'repository':mr['project'],
                'head':mr['head_sha'],'base':mr['base_sha'],'source':mr['source'],'target':mr['target']}

    def prepare(self,binding):
        # Called while the business queue transaction excludes scheduler claims.
        if any(t['project_id']==binding['team'] and t['status'] in ('queued','running','waiting_input') for t in self.store.tasks()):
            raise ValueError('Mock team is busy; retry event after its current task')
        path=Path(binding['path'])
        if self.git(path,'status','--porcelain','--untracked-files=all'):
            raise ValueError('Mock workspace is dirty; preserve and inspect it first')
        repo=self.mock.repository(binding['repository'])
        if (self.mock.ref(repo,binding['source'])!=binding['head']
                or self.mock.ref(repo,binding['target'])!=binding['base']):
            raise ValueError('Mock branches changed before dispatch')
        self.git(path,'fetch','--',str(repo),'+refs/heads/*:refs/remotes/origin/*')
        self.git(path,'checkout','--detach',binding['head'])

    def check(self,mr_id,task_id,route):
        with self.store.transaction():
            binding=self.store.meta('mock-review:'+task_id)
            task=self.store.get(task_id)
            if not binding or binding['mr_id']!=mr_id or not task:
                raise ValueError('No receiver-bound task for this mock MR')
            original=task.get('reply_to') or {}
            if any(original.get(k)!=route.get(k) for k in ('channel_id','source_user_id')):
                raise ValueError('Mock check must be requested by its configured MM owner/channel')
            if task['status'] not in ('succeeded','reported_unverified') or not task.get('evidence'):
                raise ValueError('Team has not completed passing role gates')
            evidence=Path(task['evidence'])
            reports=json.loads((evidence/'role-reports.json').read_text(encoding='utf-8'))
            for role in ('reviewer','tester'):
                latest=next((r for r in reversed(reports) if r.get('role')==role),None)
                if not latest or latest.get('verdict')!='PASS' or latest.get('process_exit_code')!=0:
                    raise ValueError('Missing successful '+role+' evidence')
            snapshot=json.loads((evidence/'final-snapshot.json').read_text(encoding='utf-8'))
            if workspace_stamp(binding['path'])!=snapshot['git']:
                raise ValueError('Workspace changed since team completion')
            if self.git(binding['path'],'rev-parse','HEAD')!=binding['head'] or self.git(binding['path'],'status','--porcelain','--untracked-files=all'):
                raise ValueError('Read-only MR review changed the workspace')
            repo=self.mock.repository(binding['repository'])
            if (self.mock.ref(repo,binding['source'])!=binding['head'] or self.mock.ref(repo,binding['target'])!=binding['base']):
                raise ValueError('Review is stale; dispatch the current version')
            self.git(binding['path'],'diff','--check',binding['base']+'...'+binding['head'])
            self.mock.record_check(mr_id,binding['head'],binding['base'],task_id,True)
            return {'verified_task':task_id,'head':binding['head'],'base':binding['base'],'scope':'Team reviewer/tester PASS, unchanged exact checkout, Git diff check; mock only'}
