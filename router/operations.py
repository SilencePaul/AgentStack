"""Independent MM operations and AgentStack-only maintenance execution."""
import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from .cli import service_lock, deliver_pending
from .diagnostics import DiagnosticRouter, is_diagnostic_query
from .service import Scheduler, Router
from .store import Store


def validate_runtimes(business, operations):
    business, operations = Path(business).resolve(), Path(operations).resolve()
    if business == operations or business.is_relative_to(operations) or operations.is_relative_to(business):
        raise ValueError('Business and operations runtimes must be separate non-overlapping directories')
    left, right = business/'router.sqlite3', operations/'router.sqlite3'
    if left.exists() and right.exists() and left.samefile(right):
        raise ValueError('Business and operations databases must be distinct')


class MaintenanceStore(Store):
    def _notice(self, reply_to, text, key, task_id=None):
        if task_id:
            text = text.replace('/router answer ', '/ops answer ').replace('/diag report ', '/ops report ')
        super()._notice(reply_to, text, key, task_id)


class Controller:
    """Control only the child this process launched, never an arbitrary PID."""
    def __init__(self, store, runtime, config, source):
        self.store = store
        self.runtime, self.config, self.source = map(Path, (runtime, config, source))
        self.child = None
        self.pending = None
        self.log = None

    def actions(self):
        with self.store.lock:
            return [(r[0], json.loads(r[1])) for r in self.store.db.execute(
                "SELECT key,value FROM metadata WHERE key LIKE 'ops.action:%' ORDER BY rowid")]

    def request(self, action, source_key, route):
        key = 'ops.action:' + (source_key or uuid.uuid4().hex)
        if not self.store.meta(key):
            self.store.set_meta(key, {'action':action, 'state':'queued', 'route':route})
        return '已记录服务控制请求；结果将在本消息线程返回。运行中的业务任务会阻止重启。'

    def status(self):
        owned = self.child is not None and self.child.poll() is None
        return '维护进程在线；托管业务Router：' + ('运行中' if owned else '未运行或不属于本进程')

    def launch(self):
        # This preflight rejects an already-running external instance. The child
        # acquires the same lock itself, so a race fails closed in that child.
        with service_lock(self.runtime / 'service.lock'):
            pass
        if self.log:
            self.log.close()
        self.log = (self.runtime / 'managed-router.log').open('ab')
        self.child = subprocess.Popen(
            [sys.executable, '-X', 'utf8', '-m', 'router.cli', '--config', str(self.config.resolve()),
             '--runtime', str(self.runtime.resolve()), 'serve'], cwd=self.source,
            stdout=self.log, stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    def begin(self, action):
        owned = self.child is not None and self.child.poll() is None
        if action in ('restart', 'stop'):
            if not owned:
                raise ValueError('不能操作非本维护进程托管的Router；停止旧实例后用 /ops start 接管。')
            business = Store(self.runtime / 'router.sqlite3')
            try:
                with business.transaction():
                    if any(t['status'] == 'running' for t in business.tasks()):
                        raise ValueError('有运行中的业务任务；请等待其完成后重启，不会强杀或重放。')
                    business.set_meta('shutdown', '1')
            finally:
                business.close()
            return 'stopping'
        if owned:
            raise ValueError('托管Router已经运行。')
        self.launch()
        return 'starting'

    def tick(self):
        if self.pending is None:
            for key, item in self.actions():
                if item['state'] != 'queued':
                    continue
                # Persist before effect: a supervisor crash never replays restart.
                item['state'] = 'executing'
                self.store.set_meta(key, item)
                try:
                    stage = self.begin(item['action'])
                    self.pending = (key, item, stage, time.time())
                except (ValueError, RuntimeError, OSError) as exc:
                    self.finish(key, item, '控制请求未执行：' + str(exc))
                break
            return
        key, item, stage, started = self.pending
        if stage == 'stopping' and self.child.poll() is not None:
            if item['action'] == 'stop':
                self.finish(key, item, '业务Router已停止；独立诊断和维护编码仍在线。')
                return
            try:
                self.launch()
                self.pending = (key, item, 'starting', time.time())
            except (RuntimeError, OSError) as exc:
                self.finish(key, item, '旧Router已停止，新进程启动失败：' + type(exc).__name__)
            return
        if stage == 'starting':
            if self.child.poll() is not None:
                self.finish(key, item, 'Router启动失败；请检查 managed-router.log，未重跑业务任务。')
                return
            business = Store(self.runtime / 'router.sqlite3')
            try:
                try:
                    ready = float(business.meta('service_heartbeat', 0)) >= started
                except (TypeError, ValueError):
                    ready = False
            finally:
                business.close()
            if ready:
                self.finish(key, item, 'Router已启动，已观察到新心跳；/diag 查看通信状态。失败任务未自动重跑。')
                return
        if time.time() - started > 45:
            self.finish(key, item, '控制请求超时；未强杀进程。请用 /ops status 和 /diag 检查，勿盲目重复重启。')

    def finish(self, key, item, text):
        with self.store.transaction():
            self.store.set_meta(key, {**item, 'state':'finished', 'result':text})
            self.store.enqueue_notice(item['route'], text, key + ':result')
        self.pending = None


class OperationsRouter:
    def __init__(self, store, controller, business_db, admin=None):
        self.store, self.controller = store, controller
        self.admin = admin
        self.business = DiagnosticRouter(business_db)
        self.maintenance = DiagnosticRouter(Path(store.db.execute('PRAGMA database_list').fetchone()[2]))

    def handle(self, text, source_key=None, reply_to=None):
        if is_diagnostic_query(text):
            return self.business.handle(text, reply_to=reply_to)
        parts = text.strip().split(maxsplit=2)
        action = parts[1] if len(parts) > 1 else 'help'
        if self.admin:
            if action == 'teams':
                return json.dumps(self.admin.catalog(),ensure_ascii=False,indent=2)
            if action == 'configure' and len(parts) == 3:
                identifier=self.admin.enqueue(parts[2],source_key,reply_to)
                return '配置请求 '+identifier+' 已记录；AI将生成草案或提问。配置不会直接生效。'
            if action == 'config' and len(parts) == 3:return self.admin.show(parts[2],reply_to)
            if action == 'apply' and len(parts) == 3:return self.admin.apply_request(parts[2],reply_to)
            if action == 'clarify' and len(parts) == 3:
                rest=parts[2].split(maxsplit=1)
                if len(rest)!=2:raise ValueError('/ops clarify 配置ID 补充说明')
                return '已记录新配置请求 '+self.admin.clarify(rest[0],rest[1],reply_to)
        if action == 'status':
            return self.controller.status() + '\n维护任务：\n' + Router(self.store).summary()
        if action in ('start', 'restart', 'stop') and len(parts) == 2:
            return self.controller.request(action, source_key, reply_to)
        if action == 'repair' and len(parts) == 3:
            task = self.store.submit('agentstack-maintenance', parts[2], source_key, reply_to)
            return '维护项目 agentstack-maintenance 已记录任务 ' + task['id'] + '；独立Qoder会话仅修改维护工作区，不自动部署。/ops report ' + task['id']
        if action in ('show', 'report') and len(parts) == 3:
            return self.maintenance.handle('/diag ' + action + ' ' + parts[2], reply_to=reply_to).replace('/diag report', '/ops report')
        if action == 'answer' and len(parts) == 3:
            return Router(self.store).handle('/router answer ' + parts[2], source_key, reply_to)
        if action == 'resume' and len(parts) == 2:
            return Router(self.store).handle('/router project-resume agentstack-maintenance', source_key, reply_to)
        return ('/ops status：维护状态\n/diag show 业务任务ID\n/diag report 业务任务ID [页码]\n'
                '/ops teams：团队配置\n/ops configure 自然语言配置目标\n/ops config 配置ID\n/ops apply 配置ID\n/ops clarify 配置ID 补充说明\n'
                '/ops start | stop | restart：托管业务Router\n/ops repair 修复目标：独立维护编码\n'
                '/ops show 维护任务ID\n/ops report 维护任务ID [页码]\n'
                '/ops answer 维护任务ID 问题ID 答案\n/ops resume：检查失败后解除维护项目暂停（不重跑失败任务）')


def main():
    from .mm import AiCrew, Poller
    from .profiles import load_profiles
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--runtime', required=True, help='Business runtime')
    parser.add_argument('--ops-runtime', required=True)
    parser.add_argument('--maintenance-path', required=True)
    parser.add_argument('--profiles', required=True)
    parser.add_argument('--projects-root', required=True, help='New repositories created through MM')
    parser.add_argument('--webhook-port', type=int, default=8788)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    runtime = Path(args.ops_runtime).resolve()
    validate_runtimes(args.runtime, runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent.parent
    maintenance = Path(args.maintenance_path).resolve(strict=True)
    if maintenance == source or maintenance.is_relative_to(source) or source.is_relative_to(maintenance):
        raise ValueError('Maintenance requires a separate checkout outside the live source tree')
    logging.basicConfig(filename=runtime/'operations.log', level=logging.INFO)
    stopping = threading.Event()
    with service_lock(runtime/'service.lock'):
        store = MaintenanceStore(runtime/'router.sqlite3', common_rules=Path(__file__).with_name('common-rules.md').read_text(encoding='utf-8'))
        existing = next((p for p in store.projects() if p['id'] == 'agentstack-maintenance'), None)
        if existing and Path(existing['path']).resolve() != maintenance:
            raise ValueError('Existing maintenance queue belongs to a different checkout')
        if not existing:
            store.add_project('agentstack-maintenance', maintenance, model=config.get('maintenance_model','Auto'),
                              rules='Only maintain AgentStack in this checkout. Do not deploy, push, restart services, inspect credentials or access business repositories. Changes require tests and a diff report; operations commands control services separately.')
        store.update_project('agentstack-maintenance', agent_profiles=load_profiles(args.profiles))
        store.recover()
        controller = Controller(store, Path(args.runtime).resolve(), Path(args.config).resolve(), source)
        for key, item in controller.actions():
            if item['state'] == 'executing':
                controller.finish(key, item, '维护进程曾中断；此控制请求未重放，请检查实际Router状态。')
        from .admin import TeamAdmin, ConfigurationPlanner
        from .webhooks import GitLabWebhook, server
        business=Store(Path(args.runtime)/'router.sqlite3',common_rules=store.common_rules)
        admin=TeamAdmin(store,business,args.projects_root,runtime/'profiles',
                        ConfigurationPlanner(config['qoder_command'],runtime/'config-plans',config.get('maintenance_model','Auto'),stopping))
        # In-flight application is never replayed after a crash. Its partial
        # checkout/config stays available for inspection.
        with store.transaction():
            for row in store.db.execute("SELECT key,value FROM metadata WHERE key LIKE 'admin:%'").fetchall():
                item=json.loads(row[1])
                if item['state'] in ('planning','applying'):
                    store.set_meta(row[0],{**item,'state':'failed','error':'Maintenance interrupted; inspect before a new draft'})
        router = OperationsRouter(store, controller, Path(args.runtime)/'router.sqlite3',admin)
        hook_server=server(('127.0.0.1',args.webhook_port),GitLabWebhook(business,webhook_secret(runtime)))
        hook_thread=threading.Thread(target=hook_server.serve_forever,daemon=True)
        hook_thread.start()
        settings = config['mm']
        client = AiCrew(settings['script'], settings['base'], cancel_event=stopping)
        poller = Poller(client, store, router, settings['channels'], settings['allowed_user_ids'], settings['bot_id'],
                        accept_query=lambda text: is_diagnostic_query(text) or text == '/ops' or text.startswith('/ops '))
        scheduler = Scheduler(store, config['qoder_command'], runtime/'runs', 1)
        def network():
            while not stopping.is_set():
                try:
                    if client.get('/users/me').get('id') != settings['bot_id']:
                        raise ValueError('Operations bot identity mismatch')
                    poller.poll()
                    deliver_pending(store, client, stopping)
                except Exception as exc:
                    logging.warning('Operations transport: %s', type(exc).__name__)
                stopping.wait(2)
        thread = threading.Thread(target=network, daemon=True)
        thread.start()
        def configuration():
            while not stopping.is_set():
                admin.tick()
                stopping.wait(.5)
        config_thread=threading.Thread(target=configuration,daemon=True)
        config_thread.start()
        try:
            while not stopping.is_set():
                controller.tick()
                scheduler.tick()
                store.set_meta('service_heartbeat', str(time.time()))
                stopping.wait(.5)
        except KeyboardInterrupt:
            pass
        finally:
            stopping.set()
            scheduler.shutdown()
            thread.join(timeout=20)
            config_thread.join(timeout=20)
            hook_server.shutdown();hook_server.server_close();hook_thread.join(timeout=5)
            store.set_meta('service_heartbeat', 'stopped')
            store.close()
            business.close()


def webhook_secret(runtime):
    """Persist a local secret; Windows uses current-user DPAPI SecureString XML."""
    if os.name == 'nt':
        path=str((runtime/'webhook-secret.xml').resolve()).replace("'","''")
        script=("$ErrorActionPreference='Stop'; $p='"+path+"'; "
                "if (Test-Path -LiteralPath $p) {$s=Import-Clixml -LiteralPath $p} else {"
                "$v=[Guid]::NewGuid().ToString('N')+[Guid]::NewGuid().ToString('N'); "
                "$s=ConvertTo-SecureString $v -AsPlainText -Force; $s | Export-Clixml -LiteralPath $p}; "
                "[System.Net.NetworkCredential]::new('', $s).Password")
        result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=15)
        if result.returncode:raise RuntimeError('Cannot access encrypted webhook secret')
        return result.stdout.strip()
    import secrets
    path=runtime/'webhook-secret'
    try:
        with path.open('x') as file:
            os.chmod(path,0o600);file.write(secrets.token_hex(32))
    except FileExistsError:pass
    return path.read_text().strip()


if __name__ == '__main__':
    main()
