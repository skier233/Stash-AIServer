# Database Testing Guide

This guide explains how to run database tests for the Stash AI Server backend.

## Quick Start

The test suite automatically handles PostgreSQL setup for you:

```bash
# Run all tests (including database tests)
python -m pytest tests/

# Run only database tests
python -m pytest -m database tests/

# Skip database tests
python -m pytest -m "not database" tests/
```

## How It Works

The test suite uses a multi-tier approach to ensure PostgreSQL is available:

### 1. Automatic Database Detection

The test suite automatically:
- Checks if PostgreSQL is already running and accessible
- Tries to connect using standard credentials (`postgres:postgres`)
- Falls back to embedded PostgreSQL if needed

### 2. Embedded PostgreSQL

If no PostgreSQL instance is found, the test suite will:
- Automatically detect PostgreSQL binaries on your system
- Start a temporary PostgreSQL instance on a random port
- Create isolated test databases
- Clean up everything when tests complete

### 3. Graceful Fallback

If PostgreSQL cannot be started:
- Database tests are automatically skipped
- Non-database tests continue to run normally
- Clear error messages explain how to fix the issue

## Installation Options

### Option 1: Use Conda (Recommended)

PostgreSQL is already included in the conda environment:

```bash
conda activate stash-ai-server
python -m pytest tests/  # Should work automatically
```

### Option 2: Install PostgreSQL Locally

**Windows:**
- Download from [postgresql.org](https://www.postgresql.org/download/windows/)
- Or use chocolatey: `choco install postgresql`

**macOS:**
```bash
brew install postgresql
brew services start postgresql
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install postgresql postgresql-contrib
sudo systemctl start postgresql
```

### Option 3: Skip Database Tests

If you don't need to run database tests:

```bash
python -m pytest -m "not database" tests/
```

## CI/GitHub Actions

For CI environments, use a PostgreSQL service container:

```yaml
services:
  postgres:
    image: postgres:16
    env:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: postgres
    options: >-
      --health-cmd pg_isready
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
    ports:
      - 5432:5432

# Set environment variables
env:
  POSTGRES_HOST: localhost
  POSTGRES_PORT: 5432
  POSTGRES_USER: postgres
  POSTGRES_PASSWORD: postgres
  CI: true
```

See `tests/ci_setup_example.yml` for a complete example.

## Test Database Isolation

Each test run uses:
- Unique database names (includes process ID)
- Isolated test data (automatic cleanup)
- Transaction-based test isolation
- Separate embedded PostgreSQL instances when needed

This ensures:
- Tests don't interfere with each other
- No pollution of development databases
- Safe parallel test execution
- Clean state for each test run

## Troubleshooting

### "Database connection failed"

This usually means PostgreSQL isn't running. The test suite will:
1. Try to start embedded PostgreSQL automatically
2. Show helpful error messages if that fails
3. Suggest installation options

### "PostgreSQL binary not found"

Install PostgreSQL using one of the methods above, or skip database tests:

```bash
python -m pytest -m "not database" tests/
```

### Tests are slow

Database tests take longer due to PostgreSQL startup. To run faster tests:

```bash
# Run only fast tests
python -m pytest -m "not database and not slow" tests/

# Use fast configuration
python -m pytest -c pytest-fast.ini tests/
```

### Permission errors

On some systems, you may need to adjust PostgreSQL permissions:

```bash
# Linux/macOS: Ensure postgres user exists
sudo -u postgres createuser --superuser $USER

# Windows: Run as administrator or adjust PostgreSQL service permissions
```

## Test Categories

- `@pytest.mark.database` - Requires PostgreSQL (auto-started if needed)
- `@pytest.mark.integration` - Multi-component tests
- `@pytest.mark.slow` - Longer-running tests
- `@pytest.mark.unit` - Fast unit tests (no database required)

## Performance

- **Embedded PostgreSQL startup**: ~3-5 seconds
- **Database test execution**: ~0.1-1 seconds per test
- **Test isolation overhead**: Minimal (transaction-based)
- **Cleanup**: Automatic (no manual intervention needed)

The embedded PostgreSQL approach provides the best balance of:
- Developer convenience (no manual setup)
- CI compatibility (works everywhere)
- Test isolation (clean state guaranteed)
- Performance (reasonable startup time)