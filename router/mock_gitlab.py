"""Local-only GitLab simulator with real Git refs and version-bound merge gates.

This is the internal storage adapter. record_check is not a public approval API;
the MM integration must derive its receipt from independently verified team work.
"""
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import uuid


class MockGitLab:
    def __init__(self, root):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(self.root/'mock.sqlite3',check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE IF NOT EXISTS mrs(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS projects(number INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL)')

    def close(self):self.db.close()

    @staticmethod
    def name(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value):
            raise ValueError('Expected a registry slug, never a filesystem path')
        return value

    @staticmethod
    def branch(value):
        if (not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,127}',value)
                or '..' in value or '//' in value or value.endswith(('/', '.lock', '.'))):
            raise ValueError('Invalid branch')
        return value

    def repository(self, name):
        path=(self.root/'repositories'/(self.name(name)+'.git')).resolve()
        if not path.is_relative_to(self.root):raise ValueError('Repository escapes mock registry')
        return path

    def git(self, repo, *args, input=None, check=True):
        command=['git','--git-dir',str(repo),'-c','user.name=AgentStack Mock','-c','user.email=mock@localhost',*args]
        # Git plumbing protocols require literal LF, including on Windows.
        result=subprocess.run(command,input=input.encode('utf-8') if input is not None else None,capture_output=True,timeout=30)
        result.stdout=result.stdout.decode('utf-8',errors='replace')
        result.stderr=result.stderr.decode('utf-8',errors='replace')
        if check and result.returncode:raise ValueError('Mock Git operation failed: '+result.stderr[:500])
        return result

    def create_project(self, name):
        with self.lock:
            repo=self.repository(name)
            if repo.exists():raise ValueError('Mock repository already exists')
            repo.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run(['git','init','--bare','--initial-branch=main',str(repo)],check=True,capture_output=True,timeout=30)
            blob=self.git(repo,'hash-object','-w','--stdin',input='Local AgentStack mock repository. No external GitLab.\n').stdout.strip()
            tree=self.git(repo,'mktree',input=f'100644 blob {blob}\tREADME.md\n').stdout.strip()
            commit=self.git(repo,'commit-tree',tree,'-m','Initialize mock project').stdout.strip()
            self.git(repo,'update-ref','refs/heads/main',commit)
            self.db.execute('INSERT INTO projects(name) VALUES (?)',(name,))
            return repo

    def project_number(self, name):
        row=self.db.execute('SELECT number FROM projects WHERE name=?',(self.name(name),)).fetchone()
        if not row:raise ValueError('Unknown mock project')
        return row[0]

    def event(self, identifier):
        mr=self.get(identifier)
        if mr['state']=='opened':mr=self.refresh(identifier)
        elif mr['state']!='merged':raise ValueError('Unsupported mock MR state')
        return {'object_kind':'merge_request','project':{'id':self.project_number(mr['project'])},
                'object_attributes':{'iid':int(identifier[:8],16)+1,'action':'update','title':'Mock MR '+identifier,
                                     'source_branch':mr['source'],'target_branch':mr['target'],
                                     'last_commit':{'id':mr['head_sha']}},'mock_mr_id':identifier,'mock_target_sha':mr['base_sha']}

    def ref(self, repo, branch):
        return self.git(repo,'rev-parse','--verify','refs/heads/'+self.branch(branch)+'^{commit}').stdout.strip()

    def get(self, identifier):
        row=self.db.execute('SELECT data FROM mrs WHERE id=?',(identifier,)).fetchone()
        if not row:raise ValueError('Unknown mock MR')
        return json.loads(row[0])

    def save(self, mr):
        self.db.execute('INSERT INTO mrs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(mr['id'],json.dumps(mr)))

    def open_mr(self, project, source, target):
        with self.lock:
            if source==target:raise ValueError('Source and target must differ')
            repo=self.repository(project)
            mr={'id':uuid.uuid4().hex,'project':project,'source':self.branch(source),'target':self.branch(target),
                'head_sha':self.ref(repo,source),'base_sha':self.ref(repo,target),'state':'opened','ci':{},'checks':[]}
            self.save(mr)
            return mr

    def refresh(self, identifier):
        with self.lock:
            mr=self.get(identifier)
            if mr['state']!='opened':raise ValueError('Only open MRs can be refreshed')
            repo=self.repository(mr['project'])
            mr.update(head_sha=self.ref(repo,mr['source']),base_sha=self.ref(repo,mr['target']))
            self.save(mr)
            return mr

    def set_ci(self, identifier, sha, status):
        if status not in ('passed','failed','pending'):raise ValueError('Invalid mock CI state')
        with self.lock:
            mr=self.get(identifier)
            if sha!=mr['head_sha']:raise ValueError('CI result belongs to a different head')
            mr['ci'][sha]=status;self.save(mr)

    def inspect(self, identifier):
        """Diagnose a current mock version without granting approval or moving refs."""
        with self.lock:
            mr=self.refresh(identifier)
            repo=self.repository(mr['project'])
            result=self.git(repo,'merge-tree','--write-tree','--name-only',mr['base_sha'],mr['head_sha'],check=False)
            if result.returncode not in (0,1):raise ValueError('Cannot inspect mock mergeability')
            return {'id':identifier,'head_sha':mr['head_sha'],'base_sha':mr['base_sha'],
                    'mergeable':result.returncode==0,'ci':mr['ci'].get(mr['head_sha'],'pending'),
                    'conflicts':result.stdout.split('\n\n',1)[0].splitlines()[1:] if result.returncode else [],
                    'grants_approval':False,'scope':'Diagnostic only; does not approve or merge'}

    def record_check(self, identifier, head, base, task_id, passed):
        with self.lock:
            mr=self.get(identifier)
            if head!=mr['head_sha'] or base!=mr['base_sha']:raise ValueError('Review belongs to an old MR version')
            mr['checks'].append({'head':head,'base':base,'task_id':task_id,'passed':passed is True})
            self.save(mr)

    def merge(self, identifier, expected_head, expected_base):
        with self.lock:
            mr=self.get(identifier)
            if (expected_head,expected_base)!=(mr['head_sha'],mr['base_sha']):raise ValueError('Stale merge request')
            repo=self.repository(mr['project'])
            current=self.ref(repo,mr['target'])
            # A retry after the Git transaction but before the final DB write
            # recognizes exactly its pre-journaled commit, never repeats merging.
            if mr.get('merge_sha') and current==mr['merge_sha']:
                mr['state']='merged';self.save(mr);return mr['merge_sha']
            if mr['state']=='merged':return mr['merge_sha']
            if current!=expected_base or self.ref(repo,mr['source'])!=expected_head:raise ValueError('Source or target changed; repeat pre-merge checks')
            if mr['ci'].get(expected_head)!='passed':raise ValueError('CI has not passed for this exact head')
            if not any(c['passed'] and c['head']==expected_head and c['base']==expected_base for c in mr['checks']):
                raise ValueError('No passing team check for this head and target')
            result=self.git(repo,'merge-tree','--write-tree',expected_base,expected_head,check=False)
            if result.returncode:raise ValueError('Merge conflict; target unchanged')
            tree=result.stdout.splitlines()[0]
            commit=self.git(repo,'commit-tree',tree,'-p',expected_base,'-p',expected_head,'-m','Merge mock MR '+identifier).stdout.strip()
            mr['merge_sha']=commit;self.save(mr)
            # Compare source and target together at the point of mutation.
            transaction=('start\nverify refs/heads/'+mr['source']+' '+expected_head+'\nupdate refs/heads/'+mr['target']+' '+commit+' '+expected_base+'\nprepare\ncommit\n')
            self.git(repo,'update-ref','--stdin',input=transaction)
            mr['state']='merged';self.save(mr)
            return commit
