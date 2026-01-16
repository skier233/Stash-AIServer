"""Plugin loader override utilities for testing."""

import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

from stash_ai_server.plugin_runtime.loader import PLUGIN_DIR


class TestPluginLoader:
    """Test plugin loader with directory override capabilities."""
    
    def __init__(self, test_plugin_dir: Optional[Path] = None):
        """Initialize test plugin loader.
        
        Args:
            test_plugin_dir: Directory containing test plugins. 
                           Defaults to backend/tests/test_plugins/
        """
        if test_plugin_dir is None:
            test_plugin_dir = Path(__file__).parent
        
        self.test_plugin_dir = test_plugin_dir
        self.original_plugin_dir = None
        self.original_env_var = None
    
    @contextmanager
    def override_plugin_directory(self):
        """Context manager to temporarily override plugin directory for tests."""
        # Store original values
        self.original_env_var = os.environ.get('AI_SERVER_PLUGINS_DIR')
        
        try:
            # Set test plugin directory
            os.environ['AI_SERVER_PLUGINS_DIR'] = str(self.test_plugin_dir)
            
            # Force reload of plugin loader module to pick up new directory
            if 'stash_ai_server.plugin_runtime.loader' in sys.modules:
                loader_module = sys.modules['stash_ai_server.plugin_runtime.loader']
                # Update the PLUGIN_DIR in the loaded module
                loader_module.PLUGIN_DIR = self.test_plugin_dir
            
            yield self.test_plugin_dir
            
        finally:
            # Restore original values
            if self.original_env_var is not None:
                os.environ['AI_SERVER_PLUGINS_DIR'] = self.original_env_var
            else:
                os.environ.pop('AI_SERVER_PLUGINS_DIR', None)
            
            # Restore original plugin directory in loader module
            if 'stash_ai_server.plugin_runtime.loader' in sys.modules:
                loader_module = sys.modules['stash_ai_server.plugin_runtime.loader']
                # Restore original PLUGIN_DIR
                env_plugins = os.getenv('AI_SERVER_PLUGINS_DIR')
                if env_plugins:
                    loader_module.PLUGIN_DIR = Path(env_plugins)
                else:
                    # Default path
                    loader_module.PLUGIN_DIR = Path(loader_module.__file__).resolve().parent.parent / 'plugins'
    
    def get_available_test_plugins(self) -> List[str]:
        """Get list of available test plugins."""
        plugins = []
        
        if not self.test_plugin_dir.exists():
            return plugins
        
        for plugin_dir in self.test_plugin_dir.iterdir():
            if plugin_dir.is_dir() and (plugin_dir / 'plugin.yml').exists():
                plugins.append(plugin_dir.name)
        
        return sorted(plugins)
    
    def copy_test_plugin_to_temp(self, plugin_name: str, temp_dir: Path) -> Path:
        """Copy a test plugin to a temporary directory.
        
        Args:
            plugin_name: Name of the test plugin to copy
            temp_dir: Temporary directory to copy to
            
        Returns:
            Path to the copied plugin directory
        """
        source_plugin_dir = self.test_plugin_dir / plugin_name
        if not source_plugin_dir.exists():
            raise FileNotFoundError(f"Test plugin {plugin_name} not found")
        
        dest_plugin_dir = temp_dir / plugin_name
        shutil.copytree(source_plugin_dir, dest_plugin_dir)
        
        return dest_plugin_dir
    
    def create_isolated_plugin_environment(self, plugin_names: List[str]) -> Path:
        """Create an isolated plugin environment with only specified plugins.
        
        Args:
            plugin_names: List of test plugin names to include
            
        Returns:
            Path to the temporary plugin directory
        """
        temp_dir = Path(tempfile.mkdtemp(prefix='test_plugins_'))
        
        for plugin_name in plugin_names:
            self.copy_test_plugin_to_temp(plugin_name, temp_dir)
        
        return temp_dir
    
    def cleanup_temp_directory(self, temp_dir: Path):
        """Clean up a temporary plugin directory."""
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


# Global test plugin loader instance
test_plugin_loader = TestPluginLoader()


@contextmanager
def isolated_test_plugins(plugin_names: List[str]):
    """Context manager for isolated test plugin environment.
    
    Args:
        plugin_names: List of test plugin names to include in isolation
        
    Yields:
        Path to the temporary plugin directory
    """
    temp_dir = None
    
    try:
        # Create isolated environment
        temp_dir = test_plugin_loader.create_isolated_plugin_environment(plugin_names)
        
        # Override plugin directory
        with test_plugin_loader.override_plugin_directory():
            # Temporarily set the plugin directory to our isolated environment
            original_test_dir = test_plugin_loader.test_plugin_dir
            test_plugin_loader.test_plugin_dir = temp_dir
            
            try:
                yield temp_dir
            finally:
                # Restore original test directory
                test_plugin_loader.test_plugin_dir = original_test_dir
    
    finally:
        # Clean up temporary directory
        if temp_dir:
            test_plugin_loader.cleanup_temp_directory(temp_dir)


def reset_all_test_plugin_states():
    """Reset state for all test plugins."""
    test_plugins = test_plugin_loader.get_available_test_plugins()
    
    for plugin_name in test_plugins:
        try:
            # Import the plugin module
            module_name = f"stash_ai_server.plugins.{plugin_name}.plugin"
            if module_name in sys.modules:
                plugin_module = sys.modules[module_name]
                
                # Call reset function if available
                if hasattr(plugin_module, 'reset_plugin_state'):
                    plugin_module.reset_plugin_state()
        
        except Exception as e:
            # Log but don't fail - some plugins might not be loaded
            import logging
            _log = logging.getLogger(__name__)
            _log.debug(f"Could not reset state for plugin {plugin_name}: {e}")


def get_test_plugin_state(plugin_name: str) -> Optional[Dict[str, Any]]:
    """Get state for a specific test plugin.
    
    Args:
        plugin_name: Name of the test plugin
        
    Returns:
        Plugin state dictionary or None if not available
    """
    try:
        module_name = f"stash_ai_server.plugins.{plugin_name}.plugin"
        if module_name in sys.modules:
            plugin_module = sys.modules[module_name]
            
            if hasattr(plugin_module, 'get_plugin_state'):
                return plugin_module.get_plugin_state()
    
    except Exception:
        pass
    
    return None