#!/usr/bin/env python3
"""
Run infrastructure tests only - fast and reliable for CI.

This script runs only the new test infrastructure tests that are optimized
for speed and don't require database connections during import.
"""

import subprocess
import sys
from pathlib import Path

def main():
    """Run infrastructure tests."""
    backend_dir = Path(__file__).parent
    
    # Test files to run (only the fast, optimized ones)
    test_files = [
        "tests/test_basic_infrastructure.py",
        "tests/test_async_utils.py"
    ]
    
    # Build pytest command
    cmd = [
        sys.executable, "-m", "pytest",
        "-c", "pytest-fast.ini",  # Use fast configuration
        "--tb=short",
        "-v"
    ] + test_files
    
    print(f"Running infrastructure tests from {backend_dir}")
    print(f"Command: {' '.join(cmd)}")
    
    # Run tests
    result = subprocess.run(cmd, cwd=backend_dir)
    
    if result.returncode == 0:
        print("\n✅ All infrastructure tests passed!")
    else:
        print(f"\n❌ Tests failed with exit code {result.returncode}")
    
    return result.returncode

if __name__ == "__main__":
    sys.exit(main())