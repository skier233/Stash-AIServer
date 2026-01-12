# Test Plugin Directory

This directory contains dedicated test plugins for comprehensive testing of the Stash AI Server plugin system. These plugins are designed specifically for testing and should not be used in production environments.

## Overview

The test plugin system provides isolated testing environments with purpose-built plugins that test various aspects of the plugin system including:

- Plugin loading and registration
- Dependency resolution and loading order
- Service registration and async task handling
- Recommender system integration
- Error handling and failure scenarios
- Plugin configuration and settings

## Test Plugin Structure

Each test plugin follows the standard plugin structure:
```
test_plugin_name/
├── plugin.yml          # Plugin manifest
├── plugin.py           # Plugin implementation
└── README.md          # Plugin-specific documentation (optional)
```

## Available Test Plugins

### Core Test Plugins

#### `base_test_plugin`
- **Purpose**: Basic plugin functionality testing
- **Features**: Simple registration/unregistration tracking
- **Dependencies**: None
- **Use Cases**: Basic plugin loading tests, plugin state tracking

#### `test_recommender`
- **Purpose**: Recommender system testing
- **Features**: Test recommender registration and execution
- **Dependencies**: None
- **Use Cases**: Recommender registration tests, recommendation generation tests

#### `test_service`
- **Purpose**: Service registration and task handling testing
- **Features**: Service registration, async task execution, concurrency testing
- **Dependencies**: None
- **Use Cases**: Service registration tests, task execution tests, basic concurrency tests

#### `test_async_tasks`
- **Purpose**: Advanced async task and concurrency testing
- **Features**: Concurrency limits, priority handling, task cancellation
- **Dependencies**: None
- **Use Cases**: Concurrency limit tests, priority queue tests, cancellation tests

### Dependency Test Plugins

#### `test_dependencies`
- **Purpose**: Dependency resolution testing
- **Features**: Tests proper dependency loading order
- **Dependencies**: `base_test_plugin`
- **Use Cases**: Dependency resolution tests, loading order validation

#### `test_circular_deps_a` & `test_circular_deps_b`
- **Purpose**: Circular dependency detection testing
- **Features**: Creates circular dependency scenario
- **Dependencies**: Each depends on the other
- **Use Cases**: Circular dependency detection tests, error handling tests

#### `test_missing_deps`
- **Purpose**: Missing dependency handling testing
- **Features**: Depends on non-existent plugins
- **Dependencies**: `nonexistent_plugin`, `another_missing_plugin` (intentionally missing)
- **Use Cases**: Missing dependency error handling tests

### Error Handling Test Plugins

#### `test_failure_plugin`
- **Purpose**: Plugin failure scenario testing
- **Features**: Simulates registration/unregistration failures
- **Dependencies**: None
- **Environment Variables**:
  - `TEST_PLUGIN_SIMULATE_FAILURE=true`: Causes registration to fail
  - `TEST_PLUGIN_SIMULATE_UNREGISTER_FAILURE=true`: Causes unregistration to fail
- **Use Cases**: Error handling tests, failure recovery tests

## Plugin Loading Override System

The test plugin system includes utilities for overriding the plugin directory during tests:

### `plugin_loader_override.py`

Provides utilities for:
- Temporarily overriding the plugin directory
- Creating isolated plugin environments
- Managing test plugin state
- Copying plugins to temporary directories

### Key Classes and Functions

#### `TestPluginLoader`
Main class for managing test plugin environments.

#### `isolated_test_plugins(plugin_names)`
Context manager for creating isolated plugin environments with only specified plugins.

#### `reset_all_test_plugin_states()`
Resets state for all test plugins to ensure clean test environments.

#### `get_test_plugin_state(plugin_name)`
Retrieves current state from a specific test plugin.

## Usage Examples

### Basic Plugin Loading Test
```python
from tests.test_plugins.plugin_loader_override import test_plugin_loader

def test_basic_plugin_loading():
    with test_plugin_loader.override_plugin_directory():
        # Test plugin loading with test plugins
        pass
```

### Isolated Plugin Environment
```python
from tests.test_plugins.plugin_loader_override import isolated_test_plugins

def test_specific_plugins():
    with isolated_test_plugins(['base_test_plugin', 'test_recommender']):
        # Test with only these two plugins available
        pass
```

### Plugin State Testing
```python
from tests.test_plugins.plugin_loader_override import get_test_plugin_state, reset_all_test_plugin_states

def test_plugin_state():
    # Reset all plugin states
    reset_all_test_plugin_states()
    
    # Load plugins and test
    # ...
    
    # Check plugin state
    state = get_test_plugin_state('base_test_plugin')
    assert state['registered'] == True
```

## Environment Variables

The test plugin system respects the following environment variables:

- `AI_SERVER_PLUGINS_DIR`: Override plugin directory path
- `TEST_PLUGIN_SIMULATE_FAILURE`: Cause test_failure_plugin to fail during registration
- `TEST_PLUGIN_SIMULATE_UNREGISTER_FAILURE`: Cause test_failure_plugin to fail during unregistration

## Plugin State Tracking

All test plugins implement a consistent state tracking interface:

### Common State Fields
- `registered`: Whether the plugin has been registered
- `unregistered`: Whether the plugin has been unregistered  
- `register_call_count`: Number of times register() was called
- `unregister_call_count`: Number of times unregister() was called

### Plugin-Specific State Fields
Each plugin may track additional state relevant to its testing purpose.

### State Management Functions
- `get_plugin_state()`: Returns current plugin state dictionary
- `reset_plugin_state()`: Resets plugin state to initial values

## Best Practices

### Test Isolation
- Always use isolated plugin environments for tests
- Reset plugin states between tests
- Use temporary directories for plugin modifications

### Error Testing
- Use `test_failure_plugin` with environment variables to simulate failures
- Test both registration and unregistration failure scenarios
- Verify proper error handling and cleanup

### Dependency Testing
- Use dependency test plugins to verify loading order
- Test both valid and invalid dependency scenarios
- Verify circular dependency detection

### Concurrency Testing
- Use `test_async_tasks` for advanced concurrency scenarios
- Test concurrency limits and priority handling
- Verify proper task cancellation behavior

## Integration with Test Suite

The test plugins integrate with the main test suite through:

1. **conftest.py**: Provides fixtures for plugin directory override
2. **Test Configuration**: Environment variable management
3. **Database Fixtures**: Isolated database environments
4. **Async Testing**: Support for async plugin operations

## Maintenance

When adding new test plugins:

1. Follow the standard plugin structure
2. Implement state tracking interface
3. Add appropriate documentation
4. Update this README with plugin details
5. Add integration tests for the new plugin

When modifying existing test plugins:

1. Maintain backward compatibility with existing tests
2. Update state tracking as needed
3. Update documentation
4. Verify all existing tests still pass