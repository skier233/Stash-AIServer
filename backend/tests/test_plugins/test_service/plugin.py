"""Test service plugin implementation."""

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
    'service_registered': False,
    'task_executions': 0,
    'last_task_params': None,
    'active_tasks': 0,
    'completed_tasks': 0,
    'failed_tasks': 0
}


class TestService:
    """Test service for testing service registration and async task handling."""
    
    def __init__(self):
        self.name = "test_service"
        self.description = "Test service for plugin testing"
        self.max_concurrent = 2
    
    async def execute_test_task(
        self, 
        task_id: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a test task."""
        global _plugin_state
        _plugin_state['task_executions'] += 1
        _plugin_state['active_tasks'] += 1
        _plugin_state['last_task_params'] = {
            'task_id': task_id,
            'params': params
        }
        
        _log.info(f"Test service executing task {task_id}")
        
        try:
            # Simulate some async work
            duration = (params or {}).get('duration', 0.1)
            await asyncio.sleep(duration)
            
            # Check if task should fail for testing
            should_fail = (params or {}).get('should_fail', False)
            if should_fail:
                _plugin_state['failed_tasks'] += 1
                raise RuntimeError("Test task failure")
            
            _plugin_state['completed_tasks'] += 1
            
            result = {
                'task_id': task_id,
                'status': 'completed',
                'result': f'Test task {task_id} completed successfully',
                'params': params,
                'metadata': {
                    'test_plugin': True,
                    'execution_count': _plugin_state['task_executions']
                }
            }
            
            _log.info(f"Test service completed task {task_id}")
            return result
            
        finally:
            _plugin_state['active_tasks'] -= 1
    
    async def execute_long_task(
        self,
        task_id: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute a long-running test task for concurrency testing."""
        global _plugin_state
        _plugin_state['task_executions'] += 1
        _plugin_state['active_tasks'] += 1
        
        try:
            # Simulate longer work for concurrency testing
            duration = (params or {}).get('duration', 1.0)
            await asyncio.sleep(duration)
            
            _plugin_state['completed_tasks'] += 1
            
            return {
                'task_id': task_id,
                'status': 'completed',
                'result': f'Long test task {task_id} completed',
                'duration': duration
            }
            
        finally:
            _plugin_state['active_tasks'] -= 1


def register():
    """Register the test service plugin."""
    global _plugin_state
    _plugin_state['registered'] = True
    _plugin_state['register_call_count'] += 1
    
    # Register the test service
    test_service = TestService()
    
    # Register service actions
    services.register_action(
        "test_task",
        test_service.execute_test_task,
        max_concurrent=test_service.max_concurrent,
        plugin_name="test_service"
    )
    
    services.register_action(
        "test_long_task", 
        test_service.execute_long_task,
        max_concurrent=1,  # Only one long task at a time
        plugin_name="test_service"
    )
    
    _plugin_state['service_registered'] = True
    
    _log.info("Test service plugin registered")


def unregister():
    """Unregister the test service plugin."""
    global _plugin_state
    _plugin_state['unregistered'] = True
    _plugin_state['unregister_call_count'] += 1
    
    # Unregister service actions
    try:
        services.unregister_by_plugin("test_service")
        _plugin_state['service_registered'] = False
    except Exception as e:
        _log.warning(f"Failed to unregister test service: {e}")
    
    _log.info("Test service plugin unregistered")


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
        'service_registered': False,
        'task_executions': 0,
        'last_task_params': None,
        'active_tasks': 0,
        'completed_tasks': 0,
        'failed_tasks': 0
    }