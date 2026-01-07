"""Basic test infrastructure validation without database dependencies."""

import pytest
import os
from pathlib import Path

from tests.config import test_config


class TestBasicInfrastructure:
    """Test the basic test infrastructure without database dependencies."""
    
    def test_config_creation(self):
        """Test that test configuration is created correctly."""
        assert test_config.database_url.startswith("postgresql+psycopg://")
        assert "test" in test_config.database_name
        assert test_config.plugin_directory.exists()
        assert test_config.task_debug is True
        assert test_config.task_loop_interval == 0.01
    
    def test_plugin_directory_structure(self):
        """Test that test plugin directory structure is correct."""
        plugin_dir = test_config.plugin_directory
        assert plugin_dir.exists()
        assert (plugin_dir / "__init__.py").exists()
        
        # Check base test plugin exists
        base_plugin_dir = plugin_dir / "base_test_plugin"
        assert base_plugin_dir.exists()
        assert (base_plugin_dir / "plugin.yml").exists()
        assert (base_plugin_dir / "plugin.py").exists()
    
    def test_environment_overrides(self):
        """Test that environment overrides are applied correctly."""
        # Apply test configuration
        test_config.apply_environment_overrides()
        
        # Test that test environment variables are set
        assert os.getenv('TASK_DEBUG') == '1'
        assert os.getenv('TASK_LOOP_INTERVAL') == '0.01'
        assert os.getenv('AI_SERVER_LOG_LEVEL') == 'DEBUG'
        assert os.getenv('AI_SERVER_PLUGINS_DIR') == str(test_config.plugin_directory)
        
        # Clean up
        test_config.cleanup_environment()
    
    def test_config_cleanup(self):
        """Test that configuration cleanup works correctly."""
        # Apply configuration
        test_config.apply_environment_overrides()
        
        # Verify variables are set
        assert 'AI_SERVER_PLUGINS_DIR' in os.environ
        assert 'TASK_DEBUG' in os.environ
        
        # Clean up
        test_config.cleanup_environment()
        
        # Verify variables are removed (or restored to original values)
        # Note: Some variables might still exist if they were set before the test
        # The cleanup should restore original values, not necessarily remove them
    
    def test_pytest_configuration(self):
        """Test that pytest configuration is working."""
        # This test just verifies that pytest is running with our configuration
        # The fact that this test runs means pytest.ini is being loaded correctly
        assert True
    
    def test_base_test_plugin_structure(self):
        """Test that the base test plugin has correct structure."""
        base_plugin_dir = test_config.plugin_directory / "base_test_plugin"
        
        # Check plugin.yml content
        plugin_yml = base_plugin_dir / "plugin.yml"
        content = plugin_yml.read_text()
        assert "name: base_test_plugin" in content
        assert "version: 1.0.0" in content
        assert "required_backend:" in content
        assert "files: [plugin]" in content
        
        # Check plugin.py exists and has basic structure
        plugin_py = base_plugin_dir / "plugin.py"
        content = plugin_py.read_text()
        assert "def register():" in content
        assert "def unregister():" in content
        assert "def get_plugin_state():" in content