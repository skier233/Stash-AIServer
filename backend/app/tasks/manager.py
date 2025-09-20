from __future__ import annotations
import asyncio
import heapq
from typing import Dict, List, Tuple, Optional, Any, Callable
from app.tasks.models import TaskRecord, TaskStatus, TaskPriority, CancelToken
from app.actions.registry import registry as action_registry
from app.actions.models import ContextInput

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
    def __init__(self):
        self.tasks: Dict[str, TaskRecord] = {}
        self.cancel_tokens: Dict[str, CancelToken] = {}
        self.queues: Dict[str, _PriorityQueue] = {}
        self.running_counts: Dict[str, int] = {}
        self._service_locks: Dict[str, asyncio.Lock] = {}
        self._listeners: List[Callable[[str, TaskRecord, dict | None], None]] = []
        self._runner_started = False

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

    def submit(self, definition, handler, ctx: ContextInput, params: dict, priority: TaskPriority) -> TaskRecord:
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
        )
        self.tasks[task.id] = task
        self.cancel_tokens[task.id] = CancelToken()
        self.queues.setdefault(service, _PriorityQueue()).push(priority, task.id)
        self.running_counts.setdefault(service, 0)
        self._service_locks.setdefault(service, asyncio.Lock())
        self._emit('queued', task, None)
        return task

    async def start(self):
        if self._runner_started:
            return
        self._runner_started = True
        asyncio.create_task(self._main_loop())

    async def _main_loop(self):
        while True:
            await asyncio.sleep(0.1)
            for service, queue in self.queues.items():
                cfg = SERVICE_CONFIG.get(service, {})
                limit = cfg.get('max_concurrent', 1)
                if self.running_counts.get(service, 0) >= limit:
                    continue
                task_id = queue.pop()
                if not task_id:
                    continue
                task = self.tasks.get(task_id)
                if not task or task.status != TaskStatus.queued:
                    continue
                asyncio.create_task(self._run_task(task))

    async def _run_task(self, task: TaskRecord):
        service = task.service
        self.running_counts[service] = self.running_counts.get(service, 0) + 1
        task.started_at = __import__('time').time()
        task.status = TaskStatus.running
        self._emit('started', task, None)
        try:
            # Removed external connectivity HEAD check to simplify initial testing and avoid httpx dependency.
            # Resolve handler again (action could have changed though unlikely)
            from app.actions.registry import registry as reg
            resolved = reg.resolve(task.action_id, task.context)
            if not resolved:
                task.status = TaskStatus.failed
                task.error = 'Action no longer available'
                self._emit('failed', task, None)
                return
            definition, handler = resolved
            token = self.cancel_tokens.get(task.id)
            result = await handler(task.context, task.params)  # type: ignore
            if token and token.is_cancelled():
                task.status = TaskStatus.cancelled
                task.finished_at = __import__('time').time()
                self._emit('cancelled', task, None)
                return
            task.result = result
            task.status = TaskStatus.completed
            task.finished_at = __import__('time').time()
            self._emit('completed', task, None)
        except Exception as e:  # pragma: no cover
            task.status = TaskStatus.failed
            task.error = f'{e.__class__.__name__}: {e}'
            task.finished_at = __import__('time').time()
            self._emit('failed', task, None)
        finally:
            self.running_counts[service] = max(0, self.running_counts.get(service, 1) - 1)

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
        if task.status == TaskStatus.queued:
            # remove from queue
            q = self.queues.get(task.service)
            if q:
                q.remove(task_id)
            task.status = TaskStatus.cancelled
            task.finished_at = __import__('time').time()
            self._emit('cancelled', task, None)
            return True
        if task.status == TaskStatus.running:
            token = self.cancel_tokens.get(task_id)
            if token:
                token.request()
            # Running task will mark itself cancelled when it checks token
            return True
        return False

manager = TaskManager()
