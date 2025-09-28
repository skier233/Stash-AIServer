from app.services.registry import ServiceBase, services
from app.actions.registry import action, registry as action_registry
from app.actions.models import ContextRule, ContextInput
from app.tasks.manager import manager as task_manager
from app.tasks.models import TaskPriority
import asyncio, time

class SlowService(ServiceBase):
    name = 'slow'
    description = 'Synthetic slow tasks for testing'
    max_concurrency = 1

    @action(
        id='slow.sleep.short',
        label='Slow Sleep Short',
        description='Short sleep task (0.2s)',
        service='slow',
        result_kind='dialog',
        contexts=[ContextRule(pages=['scenes'], selection='single')],
    )
    async def sleep_short(self, ctx: ContextInput, params: dict):
        await asyncio.sleep(0.2)
        return {'slept': 0.2}

    @action(
        id='slow.sleep.long',
        label='Slow Sleep Long',
        description='Long sleep task (default 1.0s) interruptible',
        service='slow',
        result_kind='dialog',
        contexts=[ContextRule(pages=['scenes'], selection='single')],
    )
    async def sleep_long(self, ctx: ContextInput, params: dict, task):
        total = float(params.get('seconds', 1.0))
        step = 0.05; elapsed = 0.0
        while elapsed < total:
            await asyncio.sleep(step); elapsed += step
            if getattr(task, 'cancel_requested', False):
                return {'slept': elapsed, 'interrupted': True}
        return {'slept': total, 'interrupted': False}

    @action(
        id='slow.fail',
        label='Slow Fail',
        description='Deterministically raises an exception to test failure handling',
        service='slow',
        result_kind='dialog',
        contexts=[ContextRule(pages=['scenes'], selection='single')],
    )
    async def always_fail(self, ctx: ContextInput, params: dict):
        raise RuntimeError('intentional failure for test')

    @action(
        id='slow.batch.spawn',
        label='Slow Batch Spawn',
        description='Spawn multiple long sleep child tasks',
        service='slow',
        result_kind='dialog',
        contexts=[ContextRule(pages=['scenes'], selection='multi')],
        controller=True,
    )
    async def batch_spawn(self, ctx: ContextInput, params: dict, task):
        count = int(params.get('count', 3)); duration = float(params.get('seconds', 1.0)); hold = float(params.get('hold', 0))
        reg = action_registry; spawned: list[str] = []
        for i in range(count):
            detail_ctx = ContextInput(page='scenes', entityId=f'scene-{i}', isDetailView=True, selectedIds=[])
            resolved = reg.resolve('slow.sleep.long', detail_ctx)
            if not resolved: continue
            definition, handler = resolved
            child = task_manager.submit(definition, handler, detail_ctx, {'seconds': duration}, TaskPriority.normal, group_id=task.id)
            spawned.append(child.id)
        if hold > 0:
            elapsed = 0.0; step = 0.05
            while elapsed < hold:
                await asyncio.sleep(step); elapsed += step
                if getattr(task, 'cancel_requested', False): break
        return {'spawned': spawned, 'count': len(spawned)}


def register():
    services.register(SlowService())
