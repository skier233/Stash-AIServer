# Troubleshooting IDE Test Viewer

## Problem: Tests Show as One Grouped Test "Stash-AIServer"

If your IDE test viewer shows all tests grouped under one item instead of showing individual tests, follow these steps:

### Step 1: Verify Test Discovery Works
Run this command to confirm pytest can discover individual tests:
```bash
cd backend
python -m pytest --collect-only tests/ -v
```

**Expected output:**
```
tests/test_simple_example.py::TestSimpleExample::test_simple_assertion
tests/test_simple_example.py::TestSimpleExample::test_string_operations
tests/test_basic_infrastructure.py::TestBasicInfrastructure::test_config_creation
...
```

If you see individual test names, pytest discovery is working correctly.

### Step 2: Check IDE Configuration

**For VS Code/Kiro:**

1. **Open Settings** (`Ctrl+,`)
2. **Search for "python testing"**
3. **Verify these settings:**
   - ✅ `Python › Testing: Pytest Enabled` = `true`
   - ✅ `Python › Testing: Unittest Enabled` = `false`
   - ✅ `Python › Testing: Pytest Args` = `["tests", "-v", "--tb=short"]`
   - ✅ `Python › Testing: Cwd` = `${workspaceFolder}/backend`

**Important:** The test configuration has been updated to handle both backend directory and root workspace execution contexts. The `conftest.py` file now automatically detects the execution context and imports the correct modules.

### Step 2a: Verify Import Path Fix

Test that imports work from both contexts:
```bash
# From backend directory (should work)
cd backend
python -m pytest tests/test_simple_example.py -v

# From root workspace (should also work)
cd ..
python -m pytest backend/tests/test_simple_example.py -v
```

Both commands should run successfully without import errors.

### Step 3: Refresh Test Discovery

1. **Open Command Palette** (`Ctrl+Shift+P`)
2. **Run:** `Python: Refresh Tests`
3. **Wait for discovery to complete**
4. **Check Test Explorer panel**

### Step 4: Clear Cache and Restart

1. **Delete cache directories:**
   ```bash
   cd backend
   rm -rf .pytest_cache __pycache__ tests/__pycache__
   ```

2. **Reload IDE window:**
   - `Ctrl+Shift+P` → `Developer: Reload Window`

3. **Or restart IDE completely**

### Step 5: Check Python Interpreter

1. **Open Command Palette** (`Ctrl+Shift+P`)
2. **Run:** `Python: Select Interpreter`
3. **Choose the correct Python environment** (should show pytest installed)

### Step 6: Verify Working Directory

The IDE should be running tests from the `backend/` directory. Check that:
- Your workspace is opened at the project root (contains both `backend/` and `frontend/`)
- The `python.testing.cwd` setting points to `${workspaceFolder}/backend`

### Expected Test Hierarchy

After fixing the configuration, you should see:

```
📁 tests/
├── 📄 test_basic_infrastructure.py
│   └── 🧪 TestBasicInfrastructure
│       ├── ✅ test_config_creation
│       ├── ✅ test_plugin_directory_structure
│       ├── ✅ test_environment_overrides
│       ├── ✅ test_config_cleanup
│       ├── ✅ test_pytest_configuration
│       └── ✅ test_base_test_plugin_structure
├── 📄 test_async_utils.py
│   └── 🧪 TestAsyncUtils
│       ├── ✅ test_async_test_client_creation
│       ├── ✅ test_async_websocket_session_creation
│       ├── ✅ test_task_manager_test_utils
│       ├── ✅ test_mock_websocket_manager
│       ├── ✅ test_temporary_task_service
│       ├── ✅ test_async_test_client_context_manager
│       └── ✅ test_websocket_session_message_tracking
└── 📄 test_simple_example.py
    ├── 🧪 TestSimpleExample
    │   ├── ✅ test_simple_assertion
    │   ├── ✅ test_string_operations
    │   ├── ✅ test_with_marker
    │   └── ✅ test_list_operations
    ├── ✅ test_function_level_test
    └── ✅ test_function_with_marker
```

### Still Having Issues?

If the problem persists:

1. **Check Python Output Panel** for error messages
2. **Try running a single test file:**
   ```bash
   cd backend
   python -m pytest tests/test_simple_example.py -v
   ```
3. **Verify pytest installation:**
   ```bash
   python -c "import pytest; print(pytest.__version__)"
   ```
4. **Check IDE logs** for test discovery errors

### Performance Note

The test suite is optimized for speed:
- **19 tests run in 0.19 seconds**
- **Test discovery takes 0.13 seconds**
- **Old slow tests are archived** (prefixed with `archived_`)

This ensures fast feedback in both IDE and CI environments.

## Quick Test

To verify everything is working, try running the simple example test:
```bash
cd backend
python -m pytest tests/test_simple_example.py::TestSimpleExample::test_simple_assertion -v
```

This should complete in under 0.1 seconds and show a passing test.