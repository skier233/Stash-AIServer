"""
Dependency injection setup for the application.
Provides FastAPI dependencies for core services.
"""

from functools import lru_cache
from typing import Annotated, Optional
from fastapi import Depends

from stash_ai_server.tasks.manager import TaskManager

# Global variable to allow test isolation
_test_task_manager_override: Optional[TaskManager] = None


@lru_cache()
def get_task_manager() -> TaskManager:
    """Get the TaskManager instance (singleton)."""
    # Allow test override for isolation
    if _test_task_manager_override is not None:
        return _test_task_manager_override
    return TaskManager()


def set_test_task_manager_override(manager: Optional[TaskManager]) -> None:
    """Set a test override for the task manager (for test isolation)."""
    global _test_task_manager_override
    _test_task_manager_override = manager
    # Clear the lru_cache to ensure the override takes effect
    get_task_manager.cache_clear()


# FastAPI dependency type annotations
TaskManagerDep = Annotated[TaskManager, Depends(get_task_manager)]


def configure_task_manager(task_manager: TaskManager) -> None:
    """Configure the task manager after creation (called during startup)."""
    # Check if we're in test mode
    import os
    import sys
    is_testing = (
        'pytest' in os.getenv('_', '') or 
        'pytest' in ' '.join(sys.argv) or
        os.getenv('PYTEST_CURRENT_TEST') is not None or
        'test' in ' '.join(sys.argv).lower()
    )
    
    if not is_testing:
        try:
            task_manager.reload_configuration()
        except Exception as e:
            # If configuration fails (e.g., no database), use defaults
            print(f"[task_manager] Configuration failed, using defaults: {e}")
    else:
        # In test mode, set minimal configuration without database calls
        task_manager._loop_interval = 0.01
        task_manager._debug = False
        print("[task_manager] Using test mode configuration")