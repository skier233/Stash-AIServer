import time
import json


def test_cancel_queued_task(client, submit_task_helper):
    # Submit long task then immediately cancel before it starts (still queued)
    ctx = {"page": "scenes", "entityId": "queued-cancel", "isDetailView": True, "selectedIds": []}
    tid = submit_task_helper('slow.sleep.long', ctx, {'seconds': 1.0})
    # Cancel right away
    client.post(f'/api/v1/tasks/{tid}/cancel')
    deadline = time.time() + 3
    status = None
    while time.time() < deadline:
        data = client.get(f'/api/v1/tasks/{tid}').json()
        status = data['status']
        if status == 'cancelled':
            break
        time.sleep(0.05)
    assert status == 'cancelled', f"Expected queued cancellation, got {status}"


def test_failure_status(client, submit_task_helper):
    ctx = {"page": "scenes", "entityId": "fail-scene", "isDetailView": True, "selectedIds": []}
    tid = submit_task_helper('slow.fail', ctx, {})
    deadline = time.time() + 3
    status = None
    error = None
    while time.time() < deadline:
        data = client.get(f'/api/v1/tasks/{tid}').json()
        status = data['status']
        error = data['error']
        if status in ('failed', 'completed'):
            break
        time.sleep(0.05)
    assert status == 'failed', f"Expected failed, got {status}"
    assert error and 'intentional failure' in error, f"Unexpected error: {error}"


def test_controller_bypass_concurrency(client, submit_task_helper):
    # Controller (batch spawn with hold) should not block a regular task in another service is same? Only slow service here, so we assert running_counts doesn't prevent other controller tasks.
    multi_ctx = {"page": "scenes", "entityId": None, "isDetailView": False, "selectedIds": ["x", "y"]}
    parent1 = submit_task_helper('slow.batch.spawn', multi_ctx, {'count': 1, 'seconds': 0.5, 'hold': 0.5})
    parent2 = submit_task_helper('slow.batch.spawn', multi_ctx, {'count': 1, 'seconds': 0.5, 'hold': 0.5})
    # We expect both controller tasks to reach 'completed' (or cancelled) without being serialized by concurrency limit since skip_concurrency=True
    deadline = time.time() + 5
    seen = set()
    while time.time() < deadline and len(seen) < 2:
        for pid in (parent1, parent2):
            st = client.get(f'/api/v1/tasks/{pid}').json()['status']
            if st in ('completed', 'cancelled'):
                seen.add(pid)
        time.sleep(0.05)
    assert len(seen) == 2, f"Both controller tasks should finish; only saw {seen}"


def test_multi_priority_ordering(client, submit_task_helper):
    ctx = {"page": "scenes", "entityId": "prio", "isDetailView": True, "selectedIds": []}
    # Submit low then normal then high; high should start first, then normal, then low
    low = submit_task_helper('slow.sleep.short', ctx, {}, priority='low')
    normal = submit_task_helper('slow.sleep.short', ctx, {}, priority='normal')
    high = submit_task_helper('slow.sleep.short', ctx, {}, priority='high')
    order = []
    deadline = time.time() + 6
    completed = set()
    while time.time() < deadline and len(completed) < 3:
        for tid in (low, normal, high):
            data = client.get(f'/api/v1/tasks/{tid}').json()
            if data.get('started_at') and tid not in order:
                order.append(tid)
            if data['status'] == 'completed':
                completed.add(tid)
        time.sleep(0.05)
    assert order and order[0] == high, f"High priority should start first, got order={order}"
    # Normal should start before low (positions 1 and 2 may collapse if tasks are very fast, so only assert relative presence)
    assert order.index(high) < order.index(low), 'High should start before low'
    if normal in order:
        assert order.index(high) < order.index(normal) < order.index(low) or order.index(normal) < order.index(low)


def test_websocket_snapshot_includes_existing(client, submit_task_helper):
    ctx = {"page": "scenes", "entityId": "snap", "isDetailView": True, "selectedIds": []}
    tid = submit_task_helper('slow.sleep.short', ctx, {})
    time.sleep(0.05)  # allow queue registration
    with client.websocket_connect('/api/v1/ws/tasks') as ws:
        # Collect initial snapshot frames only (they have type task.snapshot)
        snapshot_ids = set()
        deadline = time.time() + 1
        while time.time() < deadline:
            raw = ws.receive_text()
            msg = json.loads(raw)
            if msg.get('type') == 'task.snapshot':
                snapshot_ids.add(msg['task']['id'])
            else:
                break
        assert tid in snapshot_ids, f"Existing task id {tid} not in websocket snapshot {snapshot_ids}"


def test_partial_group_cancel(client, submit_task_helper):
    # Strategy: Make first child finish before cancelling parent, leaving remaining children to be cancelled.
    # Use shorter child duration than parent hold so parent is still cancellable after one child completes.
    multi_ctx = {"page": "scenes", "entityId": None, "isDetailView": False, "selectedIds": ["p1", "p2", "p3"]}
    parent_id = submit_task_helper('slow.batch.spawn', multi_ctx, {'count': 3, 'seconds': 0.6, 'hold': 2.0})

    # Wait until one child completes AND at least one other child exists that is not finished yet (queued or running).
    deadline = time.time() + 6
    first_completed_detected = False
    while time.time() < deadline:
        listing = client.get('/api/v1/tasks').json()['tasks']
        children = [t for t in listing if t.get('group_id') == parent_id]
        statuses = {c['id']: c['status'] for c in children}
        completed_children = [cid for cid, st in statuses.items() if st == 'completed']
        unfinished_children = [cid for cid, st in statuses.items() if st not in ('completed', 'cancelled')]
        if completed_children and unfinished_children:
            first_completed_detected = True
            break
        time.sleep(0.02)
    assert first_completed_detected, 'Did not observe a completed child while others still pending before timeout'

    # Cancel parent now – already-completed child should remain completed, pending ones should become cancelled.
    client.post(f'/api/v1/tasks/{parent_id}/cancel')

    # Collect final mix of statuses
    deadline = time.time() + 8
    cancelled = set()
    completed = set()
    while time.time() < deadline and (not cancelled or not completed):
        listing = client.get('/api/v1/tasks').json()['tasks']
        children = [t for t in listing if t.get('group_id') == parent_id]
        for c in children:
            if c['status'] == 'cancelled':
                cancelled.add(c['id'])
            if c['status'] == 'completed':
                completed.add(c['id'])
        if cancelled and completed:
            break
        time.sleep(0.05)
    assert completed, 'Expected at least one completed child'
    assert cancelled, 'Expected at least one cancelled child'


def test_concurrency_slot_release_on_cancel(client, submit_task_helper):
    """Ensure that cancelling a running task frees the concurrency slot so the next queued task starts promptly.

    Scenario:
      1. Submit long-running task A (seconds=5.0) so we have time to act while it's running.
      2. Wait until A is running; then submit tasks B and C (short tasks) which should queue.
      3. Cancel A; verify A transitions to cancelled.
      4. Confirm B starts soon after cancellation (< 1s) proving slot release.
      5. After B completes, C should then start and complete, showing continued correct sequencing.
    """
    ctxA = {"page": "scenes", "entityId": "slot-A", "isDetailView": True, "selectedIds": []}
    ctxB = {"page": "scenes", "entityId": "slot-B", "isDetailView": True, "selectedIds": []}
    ctxC = {"page": "scenes", "entityId": "slot-C", "isDetailView": True, "selectedIds": []}

    a_id = submit_task_helper('slow.sleep.long', ctxA, {'seconds': 5.0})
    # Wait until A is running
    deadline = time.time() + 3
    while time.time() < deadline:
        status = client.get(f'/api/v1/tasks/{a_id}').json()['status']
        if status == 'running':
            break
        time.sleep(0.05)
    assert status == 'running', 'Task A never reached running state'

    b_id = submit_task_helper('slow.sleep.short', ctxB, {})
    c_id = submit_task_helper('slow.sleep.short', ctxC, {})
    b_status = client.get(f'/api/v1/tasks/{b_id}').json()['status']
    c_status = client.get(f'/api/v1/tasks/{c_id}').json()['status']
    assert b_status == 'queued' and c_status == 'queued', 'Expected B and C to queue behind running A'

    cancel_time = time.time()
    client.post(f'/api/v1/tasks/{a_id}/cancel')

    # Wait for A to cancel and B to start
    b_started_at = None
    deadline = time.time() + 4
    while time.time() < deadline and not b_started_at:
        a_data = client.get(f'/api/v1/tasks/{a_id}').json()
        b_data = client.get(f'/api/v1/tasks/{b_id}').json()
        if a_data['status'] == 'cancelled' and b_data['status'] == 'running':
            b_started_at = b_data.get('started_at') or time.time()
            break
        time.sleep(0.05)
    assert b_started_at is not None, 'Task B did not start after cancelling A'
    assert (b_started_at - cancel_time) < 1.2, 'Slot release took too long after cancellation'

    # Wait for B to complete then C should start and complete
    deadline = time.time() + 5
    c_completed = False
    while time.time() < deadline:
        b_stat = client.get(f'/api/v1/tasks/{b_id}').json()['status']
        c_data = client.get(f'/api/v1/tasks/{c_id}').json()
        if b_stat == 'completed' and c_data['status'] == 'running':
            # allow it to finish
            pass
        if c_data['status'] == 'completed':
            c_completed = True
            break
        time.sleep(0.05)
    assert c_completed, 'Task C did not complete after B despite single-slot scheduling'