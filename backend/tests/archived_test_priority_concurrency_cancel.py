import json
import time


def _collect(ws, predicate, want, timeout=6.0):
    deadline = time.time() + timeout
    collected = []
    while time.time() < deadline and len(collected) < want:
        msg = json.loads(ws.receive_text())
        if predicate(msg):
            collected.append(msg)
    return collected


def test_priority_order(client, submit_task_helper):
    ctx = {"page": "scenes", "entityId": "scene-1", "isDetailView": True, "selectedIds": []}
    low_id = submit_task_helper('slow.sleep.short', ctx, {}, priority='low')
    high_id = submit_task_helper('slow.sleep.short', ctx, {}, priority='high')
    deadline = time.time() + 5
    started_low = started_high = None
    while time.time() < deadline and (started_low is None or started_high is None):
        for tid in (low_id, high_id):
            r = client.get(f'/api/v1/tasks/{tid}')
            data = r.json()
            if data['id'] == low_id and data.get('started_at') and started_low is None:
                started_low = data['started_at']
            if data['id'] == high_id and data.get('started_at') and started_high is None:
                started_high = data['started_at']
        time.sleep(0.05)
    assert started_high is not None and started_low is not None, 'Both tasks should start'
    assert started_high <= started_low, f"High priority task ({started_high}) should start before/at low ({started_low})"


def test_concurrency_limit_and_sequencing(client, submit_task_helper):
    ctx = {"page": "scenes", "entityId": "scene-2", "isDetailView": True, "selectedIds": []}
    first_id = submit_task_helper('slow.sleep.long', ctx, {'seconds': 0.4})
    second_id = submit_task_helper('slow.sleep.long', ctx, {'seconds': 0.1})
    deadline = time.time() + 8
    first_finished = second_started = None
    while time.time() < deadline and (first_finished is None or second_started is None):
        for tid in (first_id, second_id):
            r = client.get(f'/api/v1/tasks/{tid}')
            data = r.json()
            if tid == first_id and data.get('finished_at') and first_finished is None:
                first_finished = data['finished_at']
            if tid == second_id and data.get('started_at') and second_started is None:
                second_started = data['started_at']
        time.sleep(0.05)
    assert first_finished is not None, 'First task should finish'
    assert second_started is not None, 'Second task should start'
    assert second_started >= first_finished, 'Second started before first finished (violates concurrency=1)'


def test_cancellation(client, submit_task_helper):
    ctx = {"page": "scenes", "entityId": "scene-3", "isDetailView": True, "selectedIds": []}
    task_id = submit_task_helper('slow.sleep.long', ctx, {'seconds': 1.0})
    started = False
    deadline = time.time() + 6
    # Wait for start
    while time.time() < deadline and not started:
        data = client.get(f'/api/v1/tasks/{task_id}').json()
        if data.get('started_at'):
            started = True
            client.post(f'/api/v1/tasks/{task_id}/cancel')
        time.sleep(0.05)
    assert started, 'Task never started'
    # Wait for cancellation
    cancelled = False
    while time.time() < deadline and not cancelled:
        data = client.get(f'/api/v1/tasks/{task_id}').json()
        if data['status'] == 'cancelled':
            cancelled = True
        elif data['status'] == 'completed':  # cancellation lost; acceptable but signal
            break
        time.sleep(0.05)
    assert cancelled, 'Did not observe cancellation'
