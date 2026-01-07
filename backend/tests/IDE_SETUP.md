# IDE Test Viewer Setup

This guide explains how to set up and use the test viewer in your IDE (VS Code/Kiro).

## Quick Setup

The test infrastructure is already configured for IDE integration. You should see tests appear automatically in your test explorer.

## Test Discovery

The IDE will automatically discover tests in:
- `backend/tests/test_*.py` files
- Test classes starting with `Test*`
- Test functions starting with `test_*`

## Excluded Files

The following files are excluded from test discovery:
- `archived_test_*.py` - Old tests kept for reference
- `__pycache__/` - Python cache files
- `.pytest_cache/` - Pytest cache files

## Running Tests

### Via Test Explorer
1. Open the Test Explorer panel in your IDE
2. You should see the test hierarchy:
   ```
   📁 backend/tests/
   ├── 📄 test_basic_infrastructure.py
   │   └── 🧪 TestBasicInfrastructure
   │       ├── ✅ test_config_creation
   │       ├── ✅ test_plugin_directory_structure
   │       └── ... (more tests)
   └── 📄 test_async_utils.py
       └── 🧪 TestAsyncUtils
           ├── ✅ test_async_test_client_creation
           └── ... (more tests)
   ```
3. Click the play button next to any test or test class to run it
4. Use the debug button to debug tests with breakpoints

### Via Command Palette
- **Run All Tests**: `Python: Run All Tests`
- **Run Current File Tests**: `Python: Run Tests in Current File`
- **Debug Tests**: `Python: Debug All Tests`

### Via Terminal
```bash
# Run all tests (fast)
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_basic_infrastructure.py

# Run with coverage
python -m pytest tests/ --cov=stash_ai_server

# Run fast tests only
python -m pytest -c pytest-fast.ini tests/
```

## Debug Configuration

Pre-configured debug configurations are available:
- **Python: Fast Tests** - Runs only the optimized infrastructure tests
- **Python: All Tests** - Runs all active tests
- **Python: Pytest Current File** - Runs tests in the currently open file

## Test Markers

Tests are organized with markers for easy filtering:
- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.slow` - Slower tests (excluded in fast mode)
- `@pytest.mark.plugin` - Plugin system tests
- `@pytest.mark.database` - Database tests
- `@pytest.mark.api` - API tests
- `@pytest.mark.websocket` - WebSocket tests
- `@pytest.mark.task` - Task management tests
- `@pytest.mark.performance` - Performance tests
- `@pytest.mark.property` - Property-based tests

### Running Specific Markers
```bash
# Run only unit tests
python -m pytest -m "unit"

# Run everything except slow tests
python -m pytest -m "not slow"

# Run API and database tests
python -m pytest -m "api or database"
```

## Performance

The test suite is optimized for speed:
- **Infrastructure tests**: ~0.18 seconds for 13 tests
- **Lazy imports**: Application components loaded only when needed
- **Fast configuration**: Optimized pytest settings for CI/IDE

## Troubleshooting

## Troubleshooting

### Tests Not Appearing in Explorer
1. **Check Python interpreter**: Ensure it's set to your project's Python environment
2. **Install dependencies**: `pip install pytest pytest-asyncio`
3. **Reload window**: `Ctrl+Shift+P` → `Developer: Reload Window`
4. **Check Python output**: Look for errors in the Python output panel
5. **Refresh test discovery**: `Ctrl+Shift+P` → `Python: Refresh Tests`

### Tests Showing as One Grouped Test
This usually indicates a configuration issue. Try these steps:

1. **Check working directory**: Ensure `python.testing.cwd` is set correctly
2. **Verify pytest path**: Make sure `python.testing.pytestPath` points to the right executable
3. **Clear cache**: Delete `.pytest_cache` and `__pycache__` directories
4. **Restart IDE**: Close and reopen the IDE completely
5. **Check configuration**: Verify `.vscode/settings.json` has correct paths

**Expected test hierarchy in IDE:**
```
📁 tests/
├── 📄 test_basic_infrastructure.py
│   └── 🧪 TestBasicInfrastructure
│       ├── ✅ test_config_creation
│       ├── ✅ test_plugin_directory_structure
│       └── ... (6 tests total)
├── 📄 test_async_utils.py
│   └── 🧪 TestAsyncUtils
│       ├── ✅ test_async_test_client_creation
│       └── ... (7 tests total)
└── 📄 test_simple_example.py
    ├── 🧪 TestSimpleExample
    │   ├── ✅ test_simple_assertion
    │   └── ... (4 tests total)
    ├── ✅ test_function_level_test
    └── ✅ test_function_with_marker
```

### Manual Test Discovery Debug
Run this command to see what pytest discovers:
```bash
cd backend
python -m pytest --collect-only tests/ -v
```

You should see individual test names like:
```
tests/test_simple_example.py::TestSimpleExample::test_simple_assertion
tests/test_simple_example.py::TestSimpleExample::test_string_operations
...
```

### Slow Test Discovery
- The old tests have been archived (prefixed with `archived_`)
- Only fast, optimized tests are discovered by default
- If discovery is still slow, check for import errors in test files

### Import Errors
- Ensure `PYTHONPATH` includes the backend directory
- Check that all test dependencies are installed
- Verify the Python interpreter matches your virtual environment

## Configuration Files

The following files configure IDE test integration:
- `.vscode/settings.json` - VS Code/Kiro test settings
- `.vscode/launch.json` - Debug configurations
- `backend/pyproject.toml` - Pytest configuration
- `backend/pytest.ini` - Standard pytest settings
- `backend/pytest-fast.ini` - Fast/CI pytest settings

## Adding New Tests

When creating new tests:
1. Name files with `test_*.py` pattern
2. Use `Test*` class names
3. Use `test_*` function names
4. Add appropriate markers
5. Follow the existing patterns in `test_basic_infrastructure.py`

Example:
```python
import pytest

class TestMyFeature:
    @pytest.mark.unit
    def test_my_function(self):
        assert True
    
    @pytest.mark.integration
    def test_my_integration(self):
        assert True
```