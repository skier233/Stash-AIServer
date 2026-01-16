"""Test async tasks plugin implementation."""

import asyncio
import logging
from typing import Dict, Any, Optional
from stash_ai_server.services.registry import services

_log = logging.getLogger(__name__)

# Plugin state for testing
_plugin_state = {
    'registered': False,
    'unregistered': False,
    'register_call_count': 0,
    'unregister_call_count': 0,
    'concurrent_tasks': 0,
    'max_concurrent_reached': 0,
    'task_queue': [],
    'cancelled_tasks': 0,
    'priority_executions': {'high': 0, 'normal': 0, 'low': 0}
}


class TestAsyncTaskHandler:
    """Test async task handler for testing concurrency and priority management."""
    
    def __init__(self):
        self.name = "test_async_tasks"
        self.description = "Test async task handler for plugin testing"
    
    async def concurrent_task(
        self,
        task_id: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a task that tests concurrency limits."""
        global _plugin_state
        
        _plugin_state['concurrent_tasks'] += 1
        current_concurrent = _plugin_state['concurrent_tasks']
        
        if current_concurrent > _plugin_state['max_concurrent_reached']:
            _plugin_state['max_concurrent_reached'] = current_concurrent
        
        _log.info(f"Concurrent task {task_id} started (concurrent: {current_concurrent})")
        
        try:
            # Simulate work
            duration = (params or {}).get('duration', 0.5)
            await asyncio.sleep(duration)
            
            return {
                'task_id': task_id,
                'status': 'completed',
                'concurrent_count': current_concurrent,
                'max_reached': _plugin_state['max_concurrent_reached']
            }
            
        finally:
            _plugin_state['concurrent_tasks'] -= 1
    
    async def priority_task(
        self,
        task_id: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a task that tests priority handling."""
        global _plugin_state
        
        priority = (params or {}).get('priority', 'normal')
        _plugin_state['priority_executions'][priority] += 1
        
        _log.info(f"Priority task {task_id} with priority {priority}")
        
        # Simulate work
        duration = (params or {}).get('duration', 0.1)
        await asyncio.sleep(duration)
        
        return {
            'task_id': task_id,
            'status': 'completed',
            'priority': priority,
            'execution_order': _plugin_state['priority_executions'][priority]
        }
    
    async def cancellable_task(
        self,
        task_id: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a task that can be cancelled for testing cancellation."""
        global _plugin_state
        
        _log.info(f"Cancellable task {task_id} started")
        
        try:
            # Long-running task that can be cancelled
            duration = (params or {}).get('duration', 5.0)
            
            # Sleep in small increments to allow cancellation
            steps = int(duration * 10)  # 0.1 second steps
            for i in range(steps):
                await asyncio.sleep(0.1)
                
                # Check if we should simulate cancellation
                if (params or {}).get('cancel_after_steps', 0) > 0:
                    if i >= (params or {}).get('cancel_after_steps', 0):
                        _plugin_state['cancelled_tasks'] += 1
                        raise asyncio.CancelledError("Task cancelled for testing")
            
            return {
                'task_id': task_id,
                'status': 'completed',
                'duration': duration
            }
            
        except asyncio.CancelledError:
            _plugin_state['cancelled_tasks'] += 1
            _log.info(f"Cancellable task {task_id} was cancelled")
            raise


def register():
    """Register the test async tasks plugin."""
    global _plugin_state
    _plugin_state['registered'] = True
    _plugin_state['register_call_count'] += 1
    
    # Register the test async task handler
    handler = TestAsyncTaskHandler()
    
    # Register different types of async tasks
    services.register_action(
        "concurrent_task",
        handler.concurrent_task,
        max_concurrent=3,  # Test concurrency limit
        plugin_name="test_async_tasks"
    )
    
    services.register_action(
        "priority_task",
        handler.priority_task,
        max_concurrent=5,
        plugin_name="test_async_tasks"
    )
    
    services.register_action(
        "cancellable_task",
        handler.cancellable_task,
        max_concurrent=2,
        plugin_name="test_async_tasks"
    )
    
    _log.info("Test async tasks plugin registered")


def unregister():
    """Unregister the test async tasks plugin."""
    global _plugin_state
    _plugin_state['unregistered'] = True
    _plugin_state['unregister_call_count'] += 1
    
    # Unregister async task actions
    try:
        services.unregister_by_plugin("test_async_tasks")
    except Exception as e:
        _log.warning(f"Failed to unregister test async tasks: {e}")
    
    _log.info("Test async tasks plugin unregistered")


def get_plugin_state():
    """Get current plugin state for testing."""
    return _plugin_state.copy()


def reset_plugin_state():
    """Reset plugin state for testing."""
    global _plugin_state
    _plugin_state = {
        'registered': False,
        'unregistered': False,
        'register_call_count': 0,
        'unregister_call_count': 0,
        'concurrent_tasks': 0,
        'max_concurrent_reached': 0,
        'task_queue': [],
        'cancelled_tasks': 0,
        'priority_executions': {'high': 0, 'normal': 0, 'low': 0}
    }