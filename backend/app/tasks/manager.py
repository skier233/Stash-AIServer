from __future__ import annotations
import asyncio
import heapq
import inspect
from typing import Dict, List, Tuple, Optional, Any, Callable
import os
from app.core.system_settings import get_value as sys_get
import logging
from app.tasks.models import TaskRecord, TaskStatus, TaskPriority, CancelToken
from app.actions.registry import registry as action_registry
from app.actions.models import ContextInput
from app.db.session import SessionLocal
from app.tasks.history import TaskHistory

SERVICE_CONFIG: dict[str, dict] = {}

class _PriorityQueue:
    def __init__(self):
        self._heap: List[Tuple[int, int, str]] = []  # (priority, seq, task_id)
        self._seq = 0
    def push(self, priority: TaskPriority, task_id: str):
        heapq.heappush(self._heap, (int(priority), self._seq, task_id))
        self._seq += 1
    def pop(self) -> Optional[str]:
        if not self._heap:
            return None
        return heapq.heappop(self._heap)[2]
    def remove(self, task_id: str):
        self._heap = [entry for entry in self._heap if entry[2] != task_id]
        heapq.heapify(self._heap)
    def __len__(self):
        return len(self._heap)

class TaskManager:
    """In‑memory async task scheduler with per‑service priority queues.

    Features:
      - Per service max concurrency (controllers skip slot consumption)
      - Priority (high < normal < low numeric ordering via enum int())
      - Websocket/event listeners via simple callback list
      - Cascading cancellation (parent -> children)
      - Lightweight history persistence for top-level tasks only
    """
    def __init__(self):
        self.tasks: Dict[str, TaskRecord] = {}
        self.cancel_tokens: Dict[str, CancelToken] = {}
        self.queues: Dict[str, _PriorityQueue] = {}
        self.running_counts: Dict[str, int] = {}
        self._service_locks: Dict[str, asyncio.Lock] = {}
        self._listeners: List[Callable[[str, TaskRecord, dict | None], None]] = []
        self._runner_started = False
        # Values may be overridden at startup (main.py) after seeding system settings
        try:
            self._loop_interval = float(sys_get('TASK_LOOP_INTERVAL', 0.05))
        except Exception:
            self._loop_interval = 0.05
        try:
            dbg = sys_get('TASK_DEBUG', False)
            self._debug = bool(dbg)
        except Exception:
            self._debug = False
        if self._debug:
            logging.basicConfig(level=logging.DEBUG, format='[TASK] %(message)s')
        self._log = logging.getLogger('task_manager')

    def configure_service(self, service: str, max_concurrent: int, base_url: str | None):
        cfg = SERVICE_CONFIG.setdefault(service, {})
        cfg['max_concurrent'] = max(1, max_concurrent)
        if base_url:
            cfg['base_url'] = base_url

    def on_event(self, cb: Callable[[str, TaskRecord, dict | None], None]):
        self._listeners.append(cb)

    def _emit(self, event: str, task: TaskRecord, extra: dict | None = None):
        for cb in list(self._listeners):
            try:
                cb(event, task, extra)
            except Exception:
                pass
        if event in ('completed', 'failed', 'cancelled'):
            self._persist_history(task)

    # --- persistence -------------------------------------------------
    def _persist_history(self, task: TaskRecord):
        """Store terminal state for top-level tasks (best-effort, swallow errors)."""
        try:
            if task.group_id:  # skip children
                return
            db = SessionLocal()
            if db.query(TaskHistory).filter_by(task_id=task.id).first():
                return
            duration_ms = None
            if task.started_at and task.finished_at:
                duration_ms = int((task.finished_at - task.started_at) * 1000)
            items_sent = None
            child_count = len([t for t in self.tasks.values() if t.group_id == task.id])
            if child_count:
                items_sent = child_count
            item_id = None
            try:
                if getattr(task.context, 'isDetailView', False) and getattr(task.context, 'entityId', None):
                    item_id = str(task.context.entityId)
            except Exception:
                pass
            rec = TaskHistory(
                task_id=task.id,
                action_id=task.action_id,
                service=task.service,
                status=task.status.value,
                submitted_at=task.submitted_at,
                started_at=task.started_at,
                finished_at=task.finished_at,
                duration_ms=duration_ms,
                items_sent=items_sent,
                item_id=item_id,
                error=task.error,
            )
            db.add(rec)
            try:
                total = db.query(TaskHistory).count()
                if total > 600:
                    overflow = total - 500
                    old_ids = [r.id for r in db.query(TaskHistory).order_by(TaskHistory.created_at.asc()).limit(overflow).all()]
                    if old_ids:
                        db.query(TaskHistory).filter(TaskHistory.id.in_(old_ids)).delete(synchronize_session=False)
            except Exception:
                pass
            db.commit()
        except Exception:
            pass
        finally:
            try:
                db.close()
            except Exception:
                pass

    def submit(self, definition, handler, ctx: ContextInput, params: dict, priority: TaskPriority, *, group_id: str | None = None) -> TaskRecord:
        service = definition.service
        task = TaskRecord(
            id=__import__('uuid').uuid4().hex,
            action_id=definition.id,
            service=service,
            priority=priority,
            status=TaskStatus.queued,
            result_kind=definition.result_kind,
            submitted_at=__import__('time').time(),
            context=ctx,
            params=params,
            group_id=group_id,
            skip_concurrency=getattr(definition, 'controller', False),
        )
        self.tasks[task.id] = task
        self.cancel_tokens[task.id] = CancelToken()
        self.queues.setdefault(service, _PriorityQueue()).push(priority, task.id)
        self.running_counts.setdefault(service, 0)
        self._service_locks.setdefault(service, asyncio.Lock())
        self._emit('queued', task, None)
        if self._debug:
            self._log.debug(f"SUBMIT service={service} id={task.id} priority={priority.name} skip_concurrency={task.skip_concurrency} group={group_id}")
        return task

    async def start(self):
        if self._runner_started:
            return
        self._runner_started = True
        if self._debug:
            self._log.debug("START main loop")
        asyncio.create_task(self._main_loop())

    async def _main_loop(self):
        while True:
            await asyncio.sleep(self._loop_interval)
            for service, queue in self.queues.items():
                cfg = SERVICE_CONFIG.get(service, {})
                limit = cfg.get('max_concurrent', 1)
                # Only block if next queued task would consume concurrency (skip_concurrency tasks bypass)
                if self.running_counts.get(service, 0) >= limit:
                    if self._debug and len(queue):
                        self._log.debug(f"SKIP service={service} busy running={self.running_counts.get(service)} limit={limit} queued={len(queue)}")
                    continue
                task_id = queue.pop()
                if not task_id:
                    continue
                task = self.tasks.get(task_id)
                if not task or task.status != TaskStatus.queued:
                    continue
                if self._debug:
                    self._log.debug(f"DISPATCH service={service} task={task.id} skip_concurrency={task.skip_concurrency} running={self.running_counts.get(service)} limit={limit}")
                asyncio.create_task(self._run_task(task))

    async def _run_task(self, task: TaskRecord):
        service = task.service
        if not task.skip_concurrency:
            self.running_counts[service] = self.running_counts.get(service, 0) + 1
        task.started_at = __import__('time').time()
        task.status = TaskStatus.running
        self._emit('started', task, None)
        if self._debug:
            self._log.debug(f"STARTED task={task.id} service={service}")
        try:
            # Removed external connectivity HEAD check to simplify initial testing and avoid httpx dependency.
            # Resolve handler again (action could have changed though unlikely)
            resolved = action_registry.resolve(task.action_id, task.context)
            if not resolved:
                task.status = TaskStatus.failed
                task.error = 'Action no longer available'
                self._emit('failed', task, None)
                return
            definition, handler = resolved
            token = self.cancel_tokens.get(task.id)
            sig = inspect.signature(handler)
            if len(sig.parameters) >= 3:
                result = await handler(task.context, task.params, task)  # type: ignore
            else:
                result = await handler(task.context, task.params)  # type: ignore
            if token and token.is_cancelled():
                task.status = TaskStatus.cancelled
                task.finished_at = __import__('time').time()
                self._emit('cancelled', task, None)
                if self._debug:
                    self._log.debug(f"CANCELLED task={task.id}")
                return
            task.result = result
            task.status = TaskStatus.completed
            task.finished_at = __import__('time').time()
            self._emit('completed', task, None)
            if self._debug:
                self._log.debug(f"COMPLETED task={task.id}")
        except Exception as e:  # pragma: no cover
            task.status = TaskStatus.failed
            task.error = f'{e.__class__.__name__}: {e}'
            task.finished_at = __import__('time').time()
            self._emit('failed', task, None)
            if self._debug:
                self._log.debug(f"FAILED task={task.id} error={task.error}")
        finally:
            if not task.skip_concurrency:
                self.running_counts[service] = max(0, self.running_counts.get(service, 1) - 1)
            if self._debug:
                self._log.debug(f"RELEASE service={service} running={self.running_counts.get(service)}")

    def get(self, task_id: str) -> Optional[TaskRecord]:
        return self.tasks.get(task_id)

    def list(self, service: str | None = None, status: TaskStatus | None = None) -> List[TaskRecord]:
        vals = list(self.tasks.values())
        if service:
            vals = [t for t in vals if t.service == service]
        if status:
            vals = [t for t in vals if t.status == status]
        return sorted(vals, key=lambda t: t.submitted_at)

    def cancel(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        # Cascade: if this task has children (tasks whose group_id == task.id), cancel them too.
        children = [t for t in self.tasks.values() if t.group_id == task_id]
        if task.status == TaskStatus.queued:
            # remove from queue
            q = self.queues.get(task.service)
            if q:
                q.remove(task_id)
            task.status = TaskStatus.cancelled
            task.finished_at = __import__('time').time()
            task.cancel_requested = True
            self._emit('cancelled', task, None)
            if self._debug:
                self._log.debug(f"CANCEL immediate task={task.id}")
            for c in children:
                self.cancel(c.id)
            return True
        if task.status == TaskStatus.running:
            token = self.cancel_tokens.get(task_id)
            if token:
                token.request()
            task.cancel_requested = True
            # Running task will mark itself cancelled when it checks token
            for c in children:
                self.cancel(c.id)
            if self._debug:
                self._log.debug(f"CANCEL requested task={task.id}")
            return True
        return False

manager = TaskManager()
