"""Test configuration system with environment overrides."""

import os
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from stash_ai_server.core.config import settings as app_settings


@dataclass
class TestConfig:
    """Test configuration with environment overrides."""
    
    # Database configuration
    database_url: str
    plugin_directory: Path
    database_name: str = "stash_ai_server_test"
    
    # Task configuration
    task_debug: bool = True
    task_loop_interval: float = 0.01
    
    # Logging configuration
    log_level: str = "DEBUG"
    
    # Test-specific settings
    test_timeout: float = 30.0
    websocket_timeout: float = 5.0
    task_completion_timeout: float = 10.0
    
    @classmethod
    def create_test_config(cls, test_db_suffix: Optional[str] = None) -> "TestConfig":
        """Create test configuration with isolated database and plugin directory."""
        
        # Create unique test database name
        db_suffix = test_db_suffix or f"test_{os.getpid()}"
        test_db_name = f"stash_ai_server_{db_suffix}"
        
        # Use test database URL
        db_host = os.getenv('AI_SERVER_DB_HOST', '127.0.0.1')
        db_port = int(os.getenv('AI_SERVER_DB_PORT', '5544'))
        db_user = os.getenv('AI_SERVER_DB_USER', 'stash_ai_server')
        db_password = os.getenv('AI_SERVER_DB_PASSWORD', 'stash_ai_server')
        
        test_database_url = f"postgresql+psycopg://{db_user}:{db_password}@{db_host}:{db_port}/{test_db_name}"
        
        # Create isolated test plugin directory
        test_plugin_dir = Path(__file__).parent / "test_plugins"
        test_plugin_dir.mkdir(exist_ok=True)
        
        return cls(
            database_url=test_database_url,
            plugin_directory=test_plugin_dir,
            database_name=test_db_name,
        )
    
    def apply_environment_overrides(self) -> None:
        """Apply test configuration as environment variables."""
        os.environ['DATABASE_URL'] = self.database_url
        os.environ['AI_SERVER_PLUGINS_DIR'] = str(self.plugin_directory)
        os.environ['TASK_DEBUG'] = '1' if self.task_debug else '0'
        os.environ['TASK_LOOP_INTERVAL'] = str(self.task_loop_interval)
        os.environ['AI_SERVER_LOG_LEVEL'] = self.log_level
    
    def cleanup_environment(self) -> None:
        """Clean up test environment variables."""
        test_env_vars = [
            'DATABASE_URL',
            'AI_SERVER_PLUGINS_DIR', 
            'TASK_DEBUG',
            'TASK_LOOP_INTERVAL',
            'AI_SERVER_LOG_LEVEL'
        ]
        
        for var in test_env_vars:
            if var in os.environ:
                del os.environ[var]


# Global test configuration instance
test_config = TestConfig.create_test_config()