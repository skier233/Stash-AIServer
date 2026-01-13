#!/usr/bin/env python3
"""Command-line interface for test suite analysis."""

import sys
import asyncio
from pathlib import Path

# Add the backend directory to the path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from test_suite_analyzer.main import main

if __name__ == "__main__":
    asyncio.run(main())