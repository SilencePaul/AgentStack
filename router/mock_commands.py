"""Durable MM commands for the local mock GitLab registry only."""
import hashlib
import json
import re
import uuid


class MockCommands:
    def __init__(self, store, mock, emit=None, check=None, restart_receiver=None):
        self.store,self.mock,self.emit,self.check=store,mock,emit,check
        self.restart_receiver=restart_receiver

    def parse(self, text):
        parts=text.split()
        if not parts:raise ValueError('Expected mock command')
        action=parts[0]
        counts={'create':2,'open':4,'show':2,'inspect':2,'ci':4,'emit':3,'retry':4,'check':3,'merge':4,'restart-receiver':1}
        if action not in counts or len(parts)!=counts[action]:
            raise ValueError('mock: create PROJECT | open PROJECT SOURCE TARGET | show MR | inspect MR | ci MR SHA passed/failed/pending | emit RULE MR | retry RULE MR FAILED_TASK | check MR TASK | merge MR HEAD BASE')
        if action=='restart-receiver':return parts
        if action in ('create','open'):self.mock.name(parts[1])
        if action=='open':
            self.mock.branch(parts[2]);self.mock.branch(parts[3])
        if action in ('emit','retry'):self.mock.name(parts[1])
        if action not in ('create','open'):
            mr=parts[2] if action in ('emit','retry') else parts[1]
            if not re.fullmatch('[0-9a-f]{32}',mr):raise ValueError('Invalid mock MR id')
        if action in ('ci','merge'):
            for sha in parts[2:3] if action=='ci' else parts[2:]:
                if not re.fullmatch('[0-9a-f]{40,64}',sha):raise ValueError('Exact SHA required')
        if action=='ci' and parts[3] not in ('passed','failed','pending'):raise ValueError('Invalid mock CI status')
        if action=='check' and not re.fullmatch('[0-9a-f]{32}',parts[2]):raise ValueError('Invalid task id')
        if action=='retry' and not re.fullmatch('[0-9a-f]{32}',parts[3]):raise ValueError('Invalid failed task id')
        return parts

    def enqueue(self, text, source, route):
        parts=self.parse(text)
        key=hashlib.sha256(str(source or uuid.uuid4()).encode()).hexdigest()[:24]
        with self.store.transaction():
            if not self.store.meta('mock-command:'+key):
                self.store.set_meta('mock-command:'+key,{'state':'queued','parts':parts,'route':route})
        return key

    def execute(self, parts, route):
        action=parts[0]
        if action=='restart-receiver' and self.restart_receiver:return self.restart_receiver()
        if action=='create':
            self.mock.create_project(parts[1])
            return {'repository':'mock://'+parts[1],'project_id':self.mock.project_number(parts[1])}
        if action=='open':return self.mock.open_mr(*parts[1:])
        if action=='show':return self.mock.get(parts[1])
        if action=='inspect':return self.mock.inspect(parts[1])
        if action=='ci':
            self.mock.set_ci(*parts[1:]);return self.mock.get(parts[1])
        if action=='merge':return {'merge_sha':self.mock.merge(*parts[1:])}
        if action=='emit' and self.emit:return self.emit(parts[1],parts[2],route)
        if action=='retry' and self.emit:return self.emit(parts[1],parts[2],route,retry_task=parts[3])
        if action=='check' and self.check:return self.check(parts[1],parts[2],route)
        raise ValueError('Mock operation is not connected yet')

    def recover(self):
        # Unknown side effects must be inspected, never blindly replayed.
        with self.store.transaction():
            for row in self.store.db.execute("SELECT key,value FROM metadata WHERE key LIKE 'mock-command:%'").fetchall():
                item=json.loads(row[1])
                if item['state']=='running':
                    item.update(state='interrupted',error='Service restarted; inspect MR/project before retrying')
                    self.store.set_meta(row[0],item)
                    self.store.enqueue_notice(item['route'],item['error'],row[0]+':interrupted')

    def tick(self):
        with self.store.transaction():
            rows=self.store.db.execute("SELECT key,value FROM metadata WHERE key LIKE 'mock-command:%' ORDER BY rowid").fetchall()
            found=next(((r[0],json.loads(r[1])) for r in rows if json.loads(r[1])['state']=='queued'),None)
            if not found:return
            key,item=found
            item['state']='running';self.store.set_meta(key,item)
        try:
            result=self.execute(item['parts'],item['route'])
            item.update(state='done',result=result)
            text='模拟GitLab完成：'+json.dumps(result,ensure_ascii=False)
        except Exception as exc:
            item.update(state='failed',error=type(exc).__name__+': '+str(exc)[:500])
            text='模拟GitLab未完成：'+item['error']
        with self.store.transaction():
            self.store.set_meta(key,item)
            self.store.enqueue_notice(item['route'],text,key+':result')
