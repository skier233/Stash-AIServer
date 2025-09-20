import json
import time

def _recv_until(ws, task_id, statuses, timeout=5.0):
    seen = set()
    deadline = time.time() + timeout
    events = []
    while time.time() < deadline and not statuses.issubset(seen):
        raw = ws.receive_text()
        msg = json.loads(raw)
        if not msg.get('type', '').startswith('task.'):  # ignore snapshots etc
            continue
        t = msg['task']
        if t['id'] != task_id:
            continue
        events.append((msg['type'], t['status']))
        seen.add(t['status'])
    return events, seen


def test_websocket_lifecycle(client, submit_task_helper):
    ctx = {"page": "scenes", "entityId": "ws-scene-1", "isDetailView": True, "selectedIds": []}
    statuses = []
    with client.websocket_connect('/api/v1/ws/tasks') as ws:
        task_id = submit_task_helper('slow.sleep.short', ctx, {})
        deadline = time.time() + 5
        while time.time() < deadline:
            raw = ws.receive_text()
            msg = json.loads(raw)
            if not msg.get('type', '').startswith('task.'):
                continue
            t = msg['task']
            if t['id'] != task_id:
                continue
            statuses.append(t['status'])
            if t['status'] == 'completed':
                break
        assert 'completed' in statuses, f"Did not observe completion; statuses seen: {statuses}"
        # Optional sanity: started should appear before completed when present
        if 'started' in statuses:
            assert statuses.index('started') < statuses.index('completed')
