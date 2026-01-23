"""Tests for task manager testing utilities with real database integration."""

import pytest
import asyncio
from tests.async_utils import TaskManagerTestUtils


class TestTaskManagerUtils:
    """Test TaskManagerTestUtils functionality."""
    
    @pytest.mark.timeout(30)
    def test_task_manager_utils_structure(self):
        """Test that TaskManagerTestUtils has expected structure."""
        
        # Test that the class has expected methods
        expected_methods = [
            'get_running_task_count',
            'get_queued_task_count', 
            'get_task_history',
            'clear_task_history',
            'wait_for_task_completion',
            'wait_for_task_count',
            'wait_for_no_active_tasks',
            '_get_manager'
        ]
        
        for method_name in expected_methods:
            assert hasattr(TaskManagerTestUtils, method_name), f"Missing method: {method_name}"
            method = getattr(TaskManagerTestUtils, method_name)
            assert callable(method), f"Method {method_name} is not callable"
    
    @pytest.mark.timeout(30)
    def test_task_count_utilities_basic(self):
        """Test task count utilities without requiring full setup."""
        
        # These should work even if task manager isn't fully initialized
        running_count = TaskManagerTestUtils.get_running_task_count()
        queued_count = TaskManagerTestUtils.get_queued_task_count()
        
        assert isinstance(running_count, int)
        assert isinstance(queued_count, int)
        assert running_count >= 0
        assert queued_count >= 0
        
        # Test service-specific counts
        service_running = TaskManagerTestUtils.get_running_task_count("nonexistent_service")
        service_queued = TaskManagerTestUtils.get_queued_task_count("nonexistent_service")
        
        assert service_running == 0
        assert service_queued == 0
    
    @pytest.mark.timeout(30)
    def test_task_history_utilities_basic(self):
        """Test task history utilities without requiring database setup."""
        
        # Get history (should work even with empty database)
        history = TaskManagerTestUtils.get_task_history(limit=5)
        assert isinstance(history, list)
        
        # Clear history should not raise errors
        TaskManagerTestUtils.clear_task_history()
        
        # History should be empty after clearing
        history_after_clear = TaskManagerTestUtils.get_task_history(limit=5)
        assert isinstance(history_after_clear, list)
    
    @pytest.mark.timeout(30)
    def test_task_manager_availability_basic(self):
        """Test that task manager availability check works."""
        
        # The _get_manager method should return a manager instance or None
        manager = TaskManagerTestUtils._get_manager()
        
        # Manager might be None if not initialized, but should not raise errors
        if manager is not None:
            # Manager should have expected attributes
            assert hasattr(manager, 'tasks')
            assert hasattr(manager, 'list')
            assert hasattr(manager, 'get')
            assert hasattr(manager, 'cancel')
    
    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_wait_utilities_no_manager(self):
        """Test async wait utilities when manager is not available."""
        
        # Mock the _get_manager method to return None to simulate no manager
        from unittest.mock import patch
        
        with patch.object(TaskManagerTestUtils, '_get_manager', return_value=None):
            # These should raise RuntimeError when manager is not available
            with pytest.raises(RuntimeError, match="Task manager not available"):
                await TaskManagerTestUtils.wait_for_task_completion("fake_task_id", timeout=0.1)
            
            with pytest.raises(RuntimeError, match="Task manager not available"):
                await TaskManagerTestUtils.wait_for_task_count(0, timeout=0.1)
            
            with pytest.raises(RuntimeError, match="Task manager not available"):
                await TaskManagerTestUtils.wait_for_no_active_tasks(timeout=0.1)
    
    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_async_methods_structure(self):
        """Test that async methods have correct structure."""
        
        import inspect
        
        # Check that async methods are actually async
        async_methods = [
            'wait_for_task_completion',
            'wait_for_task_count', 
            'wait_for_no_active_tasks'
        ]
        
        for method_name in async_methods:
            method = getattr(TaskManagerTestUtils, method_name)
            assert inspect.iscoroutinefunction(method), f"{method_name} should be async"