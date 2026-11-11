"""Mattermost adapter; all network operations use the installed ai-crew CLI."""
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote


class TransportRejected(RuntimeError):
    """A policy rejection must not be retried without explicit content review."""


class AiCrew:
    def __init__(self, script, base=None, cancel_event=None):
        self.script, self.base = str(script), base
        self.cancel_event=cancel_event

    def call(self, *args):
        if self.cancel_event is not None and self.cancel_event.is_set():raise RuntimeError('Transport stopped')
        command = [sys.executable, '-X', 'utf8', self.script]
        if self.base:
            command += ['--base', self.base]
        result = subprocess.run(command + list(args), capture_output=True, text=True,
                                encoding='utf-8', timeout=15,
                                env={**{k:v for k,v in os.environ.items() if k not in ('AI_CREW_TICKET','FEISHU_TOKEN')}, 'PYTHONUTF8':'1', 'USERPROFILE':os.path.join(os.path.dirname(self.script),'isolated-profile'), 'HOME':os.path.join(os.path.dirname(self.script),'isolated-profile')})
        try:
            data = json.loads(result.stdout)
        except (ValueError, TypeError):
            data = None
        if result.returncode or not isinstance(data, dict) or data.get('ok') is not True:
            serialized = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else ''
            if re.search(r'WAF', serialized, re.I) and (data.get('http') == 403 or re.search(r'HTTP\s*403', serialized, re.I)):
                raise TransportRejected('HTTP 403 WAF rejection; automatic retry requires content review')
            raise RuntimeError(self._failure_metadata(args, result, data))
        return data

    @staticmethod
    def _failure_metadata(args, result, data):
        # Never log stdout/stderr or argv payloads: they may contain credentials
        # or model/user text. Keep protocol metadata and a bounded redacted error.
        metadata = {'command': str(args[0]) if args else '', 'exit': result.returncode}
        if isinstance(data, dict):
            if isinstance(data.get('http'), int):
                metadata['http'] = data['http']
            body = data.get('data') if isinstance(data.get('data'), dict) else data
            for field in ('error', 'error_id', 'message', '_error', '_raw'):
                value = body.get(field)
                if isinstance(value, str):
                    value = re.sub(r'(?i)bearer\s+\S+', 'Bearer [REDACTED]', value)
                    value = re.sub(r'(?i)(?:fh_u_|sk-|mm_pat_|mcp_)[A-Za-z0-9_.-]+', '[REDACTED]', value)
                    value = re.sub(r'(?i)(token|password|secret|authorization)([\s\":=]+)[^\s,;]+', r'\1=[REDACTED]', value)
                    value = re.sub(r'[A-Za-z0-9_-]{24,}', '[REDACTED-ID]', value)
                    metadata[field] = value[:300]
        if data is None:
            kinds = re.findall(r'(?m)^([A-Za-z]+(?:Error|Exception)):', result.stderr or '')
            metadata['output'] = 'invalid-json'
            if kinds:
                metadata['exception'] = kinds[-1]
        return 'ai-crew failure ' + json.dumps(metadata, ensure_ascii=True)

    def get(self, path):
        return self.call('mm-get', path, '--scrub')['data']

    def send(self, route, text, key):
        if len(text)>6500:
            parts=[text[i:i+6500] for i in range(0,len(text),6500)]
            return [self._send_one(route,f"({i+1}/{len(parts)})\n"+part,str(key)+":"+str(i)) for i,part in enumerate(parts)]
        return self._send_one(route,text,key)

    def _send_one(self, route, text, key):
        if not route.get('channel_id') or not route.get('root_id'):
            raise ValueError('Delivery requires an existing channel and root post')
        key = str(key)
        root = self.get('/posts/' + quote(route['root_id'], safe=''))
        if root.get('channel_id') != route['channel_id'] or not isinstance(root.get('create_at'), int):
            raise RuntimeError('Root does not match delivery channel')
        if not getattr(self, '_identity', None):
            self._identity = self.get('/users/me')['id']
        if not re.fullmatch(r'[A-Za-z0-9:_-]+', key):
            raise ValueError('Invalid delivery key')
        marker = '[router-delivery:' + key + ']'
        def find():
            found = None
            # Exhaust the channel to include every page of the target thread.
            for posts in pages(self, route['channel_id'], root['create_at']):
                for p in posts:
                    if (p.get('user_id') == self._identity and p.get('root_id') == route['root_id'] and
                            p.get('message', '').endswith(marker) and not p.get('delete_at')):
                        found = p
            return found
        existing = find()
        if existing:
            return existing
        if route.get('mention_id'):
            user = self.get('/users/' + quote(route['mention_id'], safe=''))
            username = user.get('username', '')
            if user.get('id') != route['mention_id'] or not re.fullmatch(r'[a-z0-9._-]+', username):
                raise ValueError('Cannot resolve MM recipient')
            text = '@' + username + ' ' + text.replace('@', '＠')
        args = ['say']
        if route.get('dm_user'):
            args += ['--dm', route['dm_user'], text + '\n\n' + marker]
        else:
            args += [route['channel_id'], text + '\n\n' + marker]
            if route.get('team'):
                args += ['--team', route['team']]
        args += ['--root', route['root_id']]
        # A timeout is deliberately propagated: the next attempt scans first.
        self.call(*args)
        delivered = find()
        if not delivered:
            raise RuntimeError('Delivery could not be verified')
        return delivered


def pages(client, channel, since=None):
    seen = set()
    before = None
    for _ in range(10000):
        path = '/channels/' + quote(channel, safe='') + '/posts?per_page=100'
        if before:
            path += '&before=' + quote(before, safe='')
        data = client.get(path)
        restricted = data.get('first_inaccessible_post_time', 0)
        if restricted and (since is None or restricted >= since):
            raise RuntimeError('History restriction intersects requested interval')
        order, posts = data.get('order'), data.get('posts')
        if not isinstance(order, list) or not isinstance(posts, dict):
            raise RuntimeError('Incomplete channel history')
        if not order:
            return
        if any(pid not in posts for pid in order) or set(order) & seen:
            raise RuntimeError('Incomplete or repeated history page')
        batch = [posts[pid] for pid in order]
        if any(p.get('channel_id') != channel or not isinstance(p.get('create_at'), int) for p in batch):
            raise RuntimeError('Invalid post channel or timestamp')
        seen.update(order)
        yield batch
        if since is not None and min(p['create_at'] for p in batch) < since:
            return
        before = order[-1]
    raise RuntimeError('History pagination limit reached')


class Poller:
    def __init__(self, client, store, router, channels, allowed_user_ids, bot_id, accept_query=None):
        self.client, self.store, self.router = client, store, router
        self.channels, self.allowed, self.bot_id = channels, set(allowed_user_ids), bot_id
        self.accept_query = accept_query

    def _accept(self, text, route):
        from .diagnostics import is_diagnostic_query
        if self.accept_query is not None:return self.accept_query(text)
        if is_diagnostic_query(text):return False
        return text == '/router' or text.startswith('/router ') or (route.get('dm_user') and text and not text.startswith('/'))

    def poll(self):
        errors = []
        for channel in self.channels:
            try:
                self._poll_channel(channel)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError('One or more channels failed polling') from errors[0]

    def _poll_channel(self, channel):
        route = dict(channel) if isinstance(channel, dict) else {'channel_id': channel}
        name = 'mm.cursor:' + route['channel_id']
        cursor = self.store.meta(name)
        fresh = []
        for batch in pages(self.client, route['channel_id'], cursor['time'] if cursor else float('inf')):
            if any(not isinstance(p.get('create_at'), int) for p in batch):
                raise RuntimeError('Missing post timestamp')
            fresh.extend(p for p in batch if cursor is None or p['create_at'] >= cursor['time'])
            if cursor is None or min(p['create_at'] for p in batch) < cursor['time']:
                break
        if cursor is None:
            newest = max((p['create_at'] for p in fresh), default=int(time.time() * 1000))
            self.store.set_meta(name, {'time': newest, 'ids': [p['id'] for p in fresh if p['create_at'] == newest]})
            return
        for p in sorted(fresh, key=lambda p: (p['create_at'], p['id'])):
            if p['create_at'] == cursor['time'] and p['id'] in cursor['ids']:
                continue
            done = 'mm.processed:' + p['id']
            with self.store.transaction():
                if self.store.meta(done):
                    continue
                text = p.get('message', '').strip()
                mention = route.get('bot_username')
                words = text.split(maxsplit=1)
                directed = (self.accept_query is None and mention and route.get('project_id')
                            and words and words[0] == '@' + mention and len(words) == 2)
                if directed:
                    text = '/router run ' + route['project_id'] + ' ' + words[1]
                if (p.get('user_id') in self.allowed and p.get('user_id') != self.bot_id
                        and not p.get('type') and not p.get('delete_at') and not p.get('edit_at')
                        and not p.get('props', {}).get('from_bot')
                        and self._accept(text, route)):
                    reply = {**route, 'root_id': p.get('root_id') or p['id'], 'source_user_id': p['user_id']}
                    if directed or text.startswith(('/router run ', '/router goal ')):
                        reply.update(mention_id=p['user_id'], final_only=True)
                    try:
                        answer = self.router.handle(text, source_key=p['id'], reply_to=reply)
                    except ValueError as exc:
                        self.store.enqueue_notice(reply, str(exc), 'command:' + p['id'])
                    else:
                        silent = directed or text.startswith(('/router run ', '/router goal ', '/router answer ', '/router clarify '))
                        if not silent:
                            self.store.enqueue_notice(reply, answer, 'command:' + p['id'])
                self.store.set_meta(done, True)
        newest = max([cursor['time']] + [p['create_at'] for p in fresh])
        ids = {p['id'] for p in fresh if p['create_at'] == newest}
        if newest == cursor['time']:
            ids.update(cursor['ids'])
        self.store.set_meta(name, {'time': newest, 'ids': sorted(ids)})
