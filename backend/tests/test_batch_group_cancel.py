import json
import time


def test_batch_group_cancel(client, submit_task_helper):
    multi_ctx = {"page": "scenes", "entityId": None, "isDetailView": False, "selectedIds": ["a", "b", "c"]}
    # Use longer child duration and a parent hold to allow cancellation window
    parent_id = submit_task_helper('slow.batch.spawn', multi_ctx, {'count': 3, 'seconds': 2.0, 'hold': 0.6})
    # Poll until children appear
    deadline = time.time() + 3
    while time.time() < deadline:
        listing = client.get('/api/v1/tasks').json()['tasks']
        if any(t.get('group_id') == parent_id for t in listing):
            break
        time.sleep(0.05)
    # Cancel parent while it's (likely) still holding
    client.post(f'/api/v1/tasks/{parent_id}/cancel')
    deadline = time.time() + 10
    cancelled_children = set()
    while time.time() < deadline:
        listing = client.get('/api/v1/tasks').json()['tasks']
        children = [t for t in listing if t.get('group_id') == parent_id]
        for c in children:
            if c['status'] == 'cancelled':
                cancelled_children.add(c['id'])
        if len(cancelled_children) >= 1:
            break
        time.sleep(0.05)
    assert len(cancelled_children) >= 1, f"Expected >=1 cancelled child, got {len(cancelled_children)}"
