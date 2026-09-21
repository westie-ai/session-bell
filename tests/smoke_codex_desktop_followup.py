"""Opt-in, one-message native desktop follow-up experiment.

Default is read-only inspection. --send-once submits ONLY to a dedicated native
desktop chat whose last user message exactly matches MARKER and reply is READY_2.
It never resumes threads, changes configuration, or retries an uncertain write.
This uses a pinned, internal desktop IPC protocol, not a supported public API.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import tempfile
import time
import uuid

MARKER = 'SessionBell 原会话追问测试第二轮：请只回复 READY_2'
READY = 'READY_2'
EXPECTED_REPLY = 'FOLLOWUP_OK_2'
FOLLOWUP = '【SessionBell 原会话追问测试第二轮】请只回复 FOLLOWUP_OK_2，不要使用工具，也不要修改任何文件。'
FRONTEND = 'webview/assets/app-initial-1b87ae739476.js'
FRONTEND_SHA = 'c87b94027faefdc31cc165975dc0f14b28e3f6d922f6a5188756c8f570f2b3d7'
SPEC = importlib.util.spec_from_file_location('sb', Path(__file__).resolve().parents[1] / 'mac/sessionbell_hook.py')
sb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sb)


def verify_build():
    with open('/Applications/ChatGPT.app/Contents/Resources/app.asar', 'rb') as f:
        header = f.read(16)
        size = struct.unpack_from('<I', header, 12)[0]
        if size > 16 * 1024 * 1024:
            raise ValueError('Unexpected application archive')
        entry = json.loads(f.read(size))
        for part in FRONTEND.split('/'):
            entry = entry['files'][part]
        f.seek(8 + struct.unpack_from('<I', header, 4)[0] + int(entry['offset']))
        digest = hashlib.sha256(f.read(entry['size'])).hexdigest()
    if digest != FRONTEND_SHA:
        raise ValueError('Desktop build changed; revalidate internal protocol before sending')


def build_followup(sid, command_id):
    params = {
        'conversationId': sid,
        'turnStart': {
            'request': {'threadId': sid, 'clientUserMessageId': command_id,
                        'input': [{'type': 'text', 'text': FOLLOWUP, 'text_elements': []}]},
            'context': {'inheritThreadSettings': True, 'attachments': [], 'commentAttachments': []},
        },
    }
    validate_followup(params)
    return params


def validate_followup(params):
    request = params['turnStart']['request']
    context = params['turnStart']['context']
    uuid.UUID(params['conversationId'])
    uuid.UUID(request['clientUserMessageId'])
    if request['threadId'] != params['conversationId']:
        raise ValueError('Mismatched conversation IDs')
    if set(request) != {'threadId', 'clientUserMessageId', 'input'}:
        raise ValueError('No configuration overrides allowed in this test')
    if request['input'] != [{'type': 'text', 'text': FOLLOWUP, 'text_elements': []}]:
        raise ValueError('Only the fixed test input with desktop text_elements is allowed')
    if context != {'inheritThreadSettings': True, 'attachments': [], 'commentAttachments': []}:
        raise ValueError('Unexpected desktop input context')


def validate_native_decoder(params):
    validate_followup(params)
    result = subprocess.run(['node', str(Path(__file__).with_name('codex_desktop_input_contract.mjs'))],
                            input=json.dumps(params), text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError('Offline native decoder check failed; no message sent')
    checks = json.loads(result.stdout)
    if not checks.get('original_bug_reproduced') or not checks.get('corrected_input_passed'):
        raise ValueError('Native decoder check incomplete; no message sent')


def frontend_error_snapshot():
    directory = Path.home() / 'Library/Logs/com.openai.codex'
    return {str(p): p.stat().st_size for p in directory.rglob('*.log')}


def new_frontend_error_count(baseline):
    directory = Path.home() / 'Library/Logs/com.openai.codex'
    count = 0
    for path in directory.rglob('*.log'):
        with path.open('rb') as f:
            size = path.stat().st_size
            offset = baseline.get(str(path), 0)
            f.seek(offset if offset <= size else 0)
            for line in f:
                if b'error boundary' in line or b'render-process-gone' in line:
                    count += 1
    return count


def read_events(path):
    result = []
    with open(path, 'rb') as f:
        for line in f:
            if not line.endswith(b'\n'):
                break
            if len(line) > 4 * 1024 * 1024:
                raise ValueError('Oversized desktop event')
            event = json.loads(line)
            if isinstance(event, dict):
                result.append(event)
    return result


def user_messages(events):
    messages = []
    for event in events:
        p = event.get('payload') or {}
        if event.get('type') == 'event_msg' and p.get('type') == 'user_message' and isinstance(p.get('message'), str):
            messages.append(p['message'].strip())
    # Some desktop builds persist user input only as response_item messages.
    if not messages:
        for event in events:
            p = event.get('payload') or {}
            if event.get('type') == 'response_item' and p.get('type') == 'message' and p.get('role') == 'user':
                messages.append('\n'.join(x['text'] for x in p.get('content', [])
                                          if isinstance(x, dict) and isinstance(x.get('text'), str)).strip())
    return messages


def projection(events):
    observer = sb.CodexDesktopObserver({})
    entry = {}
    for event in events:
        observer.consume(entry, event)
    return entry


def settings_digest(events):
    for event in reversed(events):
        p = event.get('payload') or {}
        if event.get('type') == 'turn_context':
            settings = {k: p[k] for k in ('cwd', 'model', 'effort', 'approval_policy', 'sandbox_policy') if k in p}
            return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    return None


def is_test_marker(text):
    # Conversation copy can preserve Markdown's escaped underscore literally.
    # Accept only this exact alternative, never fuzzy or substring matching.
    return text in (MARKER, MARKER.replace('_', '\\_'))


def find_target():
    observer = sb.CodexDesktopObserver({})
    matches = []
    for sid, row in (observer.index() or {}).items():
        if sid == os.environ.get('CODEX_THREAD_ID') or row['updated_at'] < time.time() - 3600:
            continue
        path = row['rollout_path']
        if not observer.metadata(path, sid):
            continue
        events = read_events(path)
        prompts = user_messages(events)
        if not prompts or not is_test_marker(prompts[-1]):
            continue
        entry = projection(events)
        if entry.get('status') == 'done' and entry.get('latest_reply', '').strip() == READY:
            matches.append((sid, path, events))
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one ready test conversation; found {len(matches)}')
    return matches[0]


class DesktopIPC:
    def __init__(self):
        path = Path(sb.codex_home()) / 'ipc/ipc.sock'
        info, parent = path.lstat(), path.parent.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
                or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or info.st_mode & 0o077 or parent.st_mode & 0o077):
            raise ValueError('Unsafe desktop socket ownership or permissions')
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(8)
        self.socket.connect(str(path))
        self.client_id = 'initializing-client'
        initial = self.request('initialize', {'clientType': 'sessionbell-followup-test'}, 0)
        if initial.get('resultType') != 'success':
            raise ValueError('Desktop initialization rejected')
        self.client_id = initial['result']['clientId']

    def close(self):
        self.socket.close()

    def send(self, message):
        data = json.dumps(message).encode()
        self.socket.sendall(struct.pack('<I', len(data)) + data)

    def exact(self, count, deadline):
        chunks = []
        while count:
            self.socket.settimeout(max(0.01, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                raise TimeoutError('Desktop IPC request timed out')
            data = self.socket.recv(count)
            if not data:
                raise EOFError('Desktop disconnected')
            chunks.append(data)
            count -= len(data)
        return b''.join(chunks)

    def request(self, method, params, version, target=None, request_id=None):
        if method not in ('initialize', 'thread-owner-discovery', 'thread-follower-start-turn'):
            raise ValueError('Method outside test scope')
        rid = request_id or str(uuid.uuid4())
        message = {'type': 'request', 'method': method, 'version': version,
                   'requestId': rid, 'sourceClientId': self.client_id,
                   'params': params, 'timeoutMs': 15000}
        if target:
            message['targetClientId'] = target
        self.send(message)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            size = struct.unpack('<I', self.exact(4, deadline))[0]
            if not 0 < size <= 8 * 1024 * 1024:
                raise ValueError('Unsupported desktop IPC frame size')
            event = json.loads(self.exact(size, deadline))
            if event.get('type') == 'client-discovery-request':
                self.send({'type': 'client-discovery-response', 'requestId': event['requestId'],
                           'response': {'canHandle': False}})
            elif event.get('type') == 'response' and event.get('requestId') == rid:
                return event
            # Unrelated broadcasts are discarded, never printed or persisted.
        raise TimeoutError('Desktop IPC request timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send-once', action='store_true')
    args = parser.parse_args()
    verify_build()
    sid, path, before = find_target()
    print(json.dumps({'test_conversation_ready': True, 'original_thread': True}), flush=True)
    if not args.send_once:
        return
    command_id = str(uuid.uuid4())
    params = build_followup(sid, command_id)
    validate_native_decoder(params)
    print(json.dumps({'offline_native_decoder_passed': True}), flush=True)
    ledger = Path(tempfile.gettempdir()) / ('sessionbell-native-followup-' + hashlib.sha256((sid + MARKER).encode()).hexdigest()[:16] + '.json')
    if ledger.exists():
        raise ValueError('A submission was already attempted for this test chat; do not retry')
    client = DesktopIPC()
    try:
        owner = client.request('thread-owner-discovery', {'hostId': 'local', 'conversationId': sid}, 1)
        target = owner.get('handledByClientId')
        if owner.get('resultType') != 'success' or not isinstance(target, str):
            raise ValueError('Original desktop owner unavailable; no message sent')
        uuid.UUID(target)
        if read_events(path) != before:
            raise ValueError('Test conversation changed during discovery; no message sent')
        errors_before = frontend_error_snapshot()
        fd = os.open(ledger, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump({'command_id': command_id, 'attempted_at': time.time(), 'no_automatic_retry': True}, f)
            f.flush()
            os.fsync(f.fileno())
        print(json.dumps({'owner_found': True, 'sending_once': True}), flush=True)
        response = client.request('thread-follower-start-turn', params, 2, target, command_id)
        print(json.dumps({'desktop_accepted': response.get('resultType') == 'success',
                          'owner_matched': response.get('handledByClientId') == target}), flush=True)
        if response.get('resultType') != 'success':
            # Keep private response text out of the tool output.
            error = str(response.get('error', ''))
            print(json.dumps({'error_category': next((x for x in ('no-client-found', 'request-version-mismatch', 'thread', 'permission', 'input') if x in error.lower()), 'unclassified')}))
            return
        if response.get('handledByClientId') != target or response.get('method') != 'thread-follower-start-turn':
            raise ValueError('Unexpected response routing; do not resend')
    finally:
        client.close()  # A follower disconnect must not interrupt desktop execution.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        events = read_events(path)
        state = projection(events)
        prompts = user_messages(events)
        started = sum((e.get('payload') or {}).get('type') == 'task_started' for e in events[len(before):])
        if state.get('desktop_terminal') != projection(before).get('desktop_terminal') and state.get('status') == 'done':
            # Allow delayed UI work/error-boundary reporting before declaring the
            # protocol leg checked. The user must still verify the visible UI.
            time.sleep(10)
            checks = {'same_thread_completed': True, 'one_followup_message': prompts.count(FOLLOWUP) == 1,
                              'one_new_turn': started == 1, 'expected_reply': state.get('latest_reply', '').strip() == EXPECTED_REPLY,
                              'settings_unchanged': settings_digest(before) is not None and settings_digest(events) == settings_digest(before),
                              'completed_after_follower_disconnect': True,
                              'no_new_frontend_errors': new_frontend_error_count(errors_before) == 0}
            print(json.dumps(checks), flush=True)
            if not all(checks.values()):
                raise SystemExit('Test failed; do not resend')
            return
        time.sleep(1)
    print(json.dumps({'completion_not_yet_confirmed': True, 'do_not_resend': True}), flush=True)


if __name__ == '__main__':
    main()
