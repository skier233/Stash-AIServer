"""Test plugins directory for isolated plugin testing.

This package provides dedicated test plugins and utilities for comprehensive
testing of the Stash AI Server plugin system.

Available test plugins:
- base_test_plugin: Basic plugin functionality testing
- test_recommender: Recommender system testing  
- test_service: Service registration and task handling testing
- test_async_tasks: Advanced async task and concurrency testing
- test_dependencies: Dependency resolution testing
- test_circular_deps_a/b: Circular dependency detection testing
- test_missing_deps: Missing dependency handling testing
- test_failure_plugin: Plugin failure scenario testing

Key utilities:
- plugin_loader_override: Plugin directory override and isolation utilities
- TestPluginLoader: Main class for managing test plugin environments
- isolated_test_plugins: Context manager for isolated plugin testing
"""

from .plugin_loader_override import (
    TestPluginLoader,
    test_plugin_loader,
    isolated_test_plugins,
    reset_all_test_plugin_states,
    get_test_plugin_state
)

__all__ = [
    'TestPluginLoader',
    'test_plugin_loader', 
    'isolated_test_plugins',
    'reset_all_test_plugin_states',
    'get_test_plugin_state'
]