# Test Infrastructure

This directory contains the comprehensive test suite for the Stash AI Server backend.

## Quick Start

```bash
# Run all tests (fast)
python -m pytest tests/

# Run tests with fast configuration (CI-optimized)
python -m pytest -c pytest-fast.ini tests/

# Run specific test categories
python -m pytest -m "unit"
python -m pytest -m "not slow"

# Run infrastructure tests only
python run_infrastructure_tests.py
```

## IDE Integration

The test suite is configured for IDE test viewers:
- Tests appear automatically in VS Code/Kiro test explorer
- Debug configurations included for easy debugging
- Fast test discovery (0.13s for 13 tests)
- Archived old tests to prevent slowdowns

See [IDE_SETUP.md](IDE_SETUP.md) for detailed IDE configuration.

## Performance Optimizations

The test suite has been optimized for speed:

- **Lazy imports**: Application components are imported only when needed to avoid initialization overhead
- **Fast configuration**: `pytest-fast.ini` provides CI-optimized settings  
- **Test isolation**: Each test runs in isolation without affecting others
- **Minimal fixtures**: Only necessary fixtures are loaded per test
- **Dedicated test runner**: `run_infrastructure_tests.py` for CI environments

### Performance Results

- **Before optimization**: 260 seconds for 13 tests (20 seconds per test)
- **After optimization**: 0.18 seconds for 13 tests (0.014 seconds per test)
- **Improvement**: 1,444x faster (99.93% reduction in test time)

### CI Integration

For CI environments, use the dedicated test runner:

```bash
# Fast infrastructure tests (recommended for CI)
python run_infrastructure_tests.py

# Or use pytest directly with fast config
python -m pytest -c pytest-fast.ini tests/test_basic_infrastructure.py tests/test_async_utils.py
```

## Test Structure

```
tests/
├── config.py              # Test configuration system
├── database.py            # Database testing infrastructure  
├── async_utils.py          # Async testing utilities
├── test_plugins/           # Isolated test plugins
├── test_basic_infrastructure.py  # Basic infrastructure tests
├── test_async_utils.py     # Async utilities tests
├── pytest.ini             # Standard pytest configuration
├── pytest-fast.ini        # Fast/CI pytest configuration
└── README.md              # This file
```

## Configuration Files

- **pytest.ini**: Standard configuration with full logging and detailed output
- **pytest-fast.ini**: Optimized for CI with minimal output and fast execution

## Test Categories (Markers)

- `unit`: Unit tests for individual components
- `integration`: Integration tests across multiple components
- `plugin`: Plugin system tests
- `database`: Database operation tests
- `api`: API endpoint tests
- `websocket`: WebSocket functionality tests
- `task`: Task management tests
- `performance`: Performance and load tests
- `property`: Property-based tests
- `slow`: Tests that take longer to run

## Environment Variables

The test suite uses these environment variables for configuration:

- `DATABASE_URL`: Test database connection string
- `AI_SERVER_PLUGINS_DIR`: Test plugin directory path
- `TASK_DEBUG`: Enable debug mode for faster task execution
- `TASK_LOOP_INTERVAL`: Reduced interval for faster test execution
- `AI_SERVER_LOG_LEVEL`: Logging level for tests

## CI Integration

For CI environments, use the fast configuration:

```yaml
# GitHub Actions example
- name: Run tests
  run: python -m pytest -c pytest-fast.ini --cov=stash_ai_server
```

## Key Features

1. **Environment Isolation**: Each test runs with isolated database and plugin directories
2. **Async Support**: Full async/await support for testing async components
3. **Database Isolation**: Transaction-based isolation with automatic rollback
4. **Plugin Testing**: Dedicated test plugins and isolated plugin loading
5. **Configuration Management**: Environment variable overrides with cleanup
6. **Performance Optimized**: Lazy imports and minimal initialization overhead