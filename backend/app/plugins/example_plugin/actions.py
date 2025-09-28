from app.actions.registry import action, ContextRule
from app.actions.models import ContextInput
from app.services.registry import ServiceBase, services


class ExamplePluginService(ServiceBase):
    name = 'example_plugin'
    description = 'Example plugin service'
    server_url = None
    max_concurrency = 1

    @action(
        id='example_plugin.hello',
        label='Example Hello',
        description='Demo action from example plugin',
        service='example_plugin',
        contexts=[ContextRule(pages=['scenes'], selection='single')],  # sample: detail view
        result_kind='dialog'
    )
    async def hello(self, ctx: ContextInput, params: dict):
        target = ctx.entity_id or (ctx.selected_ids[0] if ctx.selected_ids else None)
        return {
            'message': 'hello from example plugin',
            'target': target
        }


def register():
    services.register(ExamplePluginService())
