from __future__ import annotations
import asyncio
from typing import Any
from app.services.registry import ServiceBase, services
from app.actions.registry import action, registry as action_registry
from app.actions.models import ContextRule, ContextInput
from app.tasks.manager import manager as task_manager
from app.tasks.models import TaskPriority


class SlowService(ServiceBase):
    """Service with deliberately slow actions to exercise scheduling, priority, concurrency and cancellation."""
    name = 'slow'
    description = 'Synthetic slow tasks for testing'
    max_concurrency = 1  # enforce single concurrency to validate queue behaviour

    # Simple sleep action (short)
    @action(
        id='slow.sleep.short',
        label='Slow Sleep Short',
        description='Short sleep task (0.2s)',
        service='slow',
            result_kind='none',
        contexts=[ContextRule(pages=['scenes'], selection='single')],
    )
    async def sleep_short(self, ctx: ContextInput, params: dict) -> Any:
        await asyncio.sleep(0.2)
        return {'slept': 0.2}

    # Longer sleep task – loops in small steps so cancellation latency is low.
    @action(
        id='slow.sleep.long',
        label='Slow Sleep Long',
        description='Long sleep task (default 1.0s) interruptible',
        service='slow',
            result_kind='none',
        contexts=[ContextRule(pages=['scenes'], selection='single')],
    )
    async def sleep_long(self, ctx: ContextInput, params: dict, task) -> Any:  # task passed via introspection
        total = float(params.get('seconds', 1.0))
        step = 0.05
        elapsed = 0.0
        while elapsed < total:
            await asyncio.sleep(step)
            elapsed += step
            # If cancellation requested, exit early (manager will mark cancelled)
            if getattr(task, 'cancel_requested', False):
                return {'slept': elapsed, 'interrupted': True}
        return {'slept': total, 'interrupted': False}

    @action(
        id='slow.fail',
        label='Slow Fail',
        description='Deterministically raises an exception to test failure handling',
        service='slow',
        result_kind='none',
        contexts=[ContextRule(pages=['scenes'], selection='single')],
    )
    async def always_fail(self, ctx: ContextInput, params: dict) -> Any:
        raise RuntimeError('intentional failure for test')

    # Controller action that spawns multiple long sleep children (group test)
    @action(
        id='slow.batch.spawn',
        label='Slow Batch Spawn',
        description='Spawn multiple long sleep child tasks',
        service='slow',
            result_kind='none',
        contexts=[ContextRule(pages=['scenes'], selection='multi')],
        controller=True,
    )
    async def batch_spawn(self, ctx: ContextInput, params: dict, task):  # controller receives task
        count = int(params.get('count', 3))
        duration = float(params.get('seconds', 1.0))
        hold = float(params.get('hold', 0))  # optional delay (seconds) to keep parent running for cancellation tests
        reg = action_registry
        spawned: list[str] = []
        for i in range(count):
            detail_ctx = ContextInput(page='scenes', entityId=f'scene-{i}', isDetailView=True, selectedIds=[])
            resolved = reg.resolve('slow.sleep.long', detail_ctx)
            if not resolved:
                continue
            definition, handler = resolved
            child = task_manager.submit(definition, handler, detail_ctx, {'seconds': duration}, TaskPriority.normal, group_id=task.id)
            spawned.append(child.id)
        # Optional hold to allow external cancellation of parent while children run
        if hold > 0:
            elapsed = 0.0
            step = 0.05
            while elapsed < hold:
                await asyncio.sleep(step)
                elapsed += step
                if getattr(task, 'cancel_requested', False):
                    break
        return {'spawned': spawned, 'count': len(spawned)}


def register():
    services.register(SlowService())
