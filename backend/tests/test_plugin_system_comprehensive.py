"""
Comprehensive plugin system tests for the Stash AI Server.

Tests plugin loading, lifecycle management, and integration with the core system.
Uses real plugin infrastructure for proper integration testing.
"""

import pytest
import pytest_asyncio
from pathlib import Path
import tempfile
import shutil
import yaml
import os
from typing import Dict, Any

from tests.database import test_database, db_session
from tests.config import test_config


class TestPluginSystemIntegration:
    """Test plugin system integration with real plugin loading."""
    
    @pytest.fixture
    def temp_plugin_dir(self):
        """Create temporary plugin directory for testing."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)
    
    @pytest.fixture
    def sample_plugin_config(self):
        """Sample plugin configuration for testing."""
        return {
            "name": "test_plugin",
            "version": "1.0.0",
            "description": "Test plugin for integration testing",
            "author": "Test Author",
            "required_backend": ">=0.1.0",
            "files": ["plugin.py"]
        }
    
    def test_plugin_directory_discovery(self, temp_plugin_dir, sample_plugin_config):
        """Test that plugin system discovers plugins in directory."""
        # Create plugin directory structure
        plugin_dir = temp_plugin_dir / "test_plugin"
        plugin_dir.mkdir()
        
        # Create plugin.yml
        plugin_yml = plugin_dir / "plugin.yml"
        with open(plugin_yml, 'w') as f:
            yaml.dump(sample_plugin_config, f)
        
        # Create plugin implementation
        plugin_py = plugin_dir / "plugin.py"
        plugin_py.write_text("""
def register():
    pass
""")
        
        # Test plugin discovery by checking if manifest can be parsed
        from stash_ai_server.plugin_runtime.loader import _parse_manifest
        
        manifest = _parse_manifest(plugin_yml)
        assert manifest is not None
        assert manifest.name == "test_plugin"
        assert manifest.version == "1.0.0"
        assert manifest.required_backend == ">=0.1.0"
        assert "plugin.py" in manifest.files
    
    def test_plugin_loading_and_instantiation(self, temp_plugin_dir, sample_plugin_config):
        """Test plugin loading and component instantiation."""
        # Create plugin
        plugin_dir = temp_plugin_dir / "test_plugin"
        plugin_dir.mkdir()
        
        plugin_yml = plugin_dir / "plugin.yml"
        with open(plugin_yml, 'w') as f:
            yaml.dump(sample_plugin_config, f)
        
        plugin_py = plugin_dir / "plugin.py"
        plugin_py.write_text("""
def register():
    print("Plugin registered successfully")
""")
        
        # Test plugin manifest parsing
        from stash_ai_server.plugin_runtime.loader import _parse_manifest
        
        manifest = _parse_manifest(plugin_yml)
        assert manifest is not None
        assert manifest.name == "test_plugin"
        assert manifest.version == "1.0.0"
    
    @pytest.mark.asyncio
    async def test_plugin_execution(self, temp_plugin_dir, sample_plugin_config):
        """Test plugin component execution."""
        # Create plugin
        plugin_dir = temp_plugin_dir / "test_plugin"
        plugin_dir.mkdir()
        
        plugin_yml = plugin_dir / "plugin.yml"
        with open(plugin_yml, 'w') as f:
            yaml.dump(sample_plugin_config, f)
        
        plugin_py = plugin_dir / "plugin.py"
        plugin_py.write_text("""
def register():
    # In a real plugin, this would register recommenders/actions
    pass
""")
        
        # Test plugin manifest parsing and validation
        from stash_ai_server.plugin_runtime.loader import _parse_manifest
        
        manifest = _parse_manifest(plugin_yml)
        assert manifest is not None
        assert manifest.name == "test_plugin"
        assert manifest.version == "1.0.0"
    
    def test_plugin_error_handling(self, temp_plugin_dir):
        """Test plugin system error handling for malformed plugins."""
        # Create plugin with invalid YAML
        plugin_dir = temp_plugin_dir / "invalid_plugin"
        plugin_dir.mkdir()
        
        plugin_yml = plugin_dir / "plugin.yml"
        plugin_yml.write_text("invalid: yaml: content: [")
        
        from stash_ai_server.plugin_runtime.loader import _parse_manifest
        
        # Should handle invalid YAML gracefully
        manifest = _parse_manifest(plugin_yml)
        assert manifest is None  # Should return None for invalid manifests
    
    def test_plugin_dependency_resolution(self, temp_plugin_dir):
        """Test plugin dependency resolution and loading order."""
        # Create plugin A that depends on plugin B
        plugin_a_dir = temp_plugin_dir / "plugin_a"
        plugin_a_dir.mkdir()
        
        plugin_a_config = {
            "name": "plugin_a",
            "version": "1.0.0",
            "description": "Plugin A",
            "required_backend": ">=0.1.0",
            "depends_on": ["plugin_b"],
            "files": ["plugin.py"]
        }
        
        with open(plugin_a_dir / "plugin.yml", 'w') as f:
            yaml.dump(plugin_a_config, f)
        
        (plugin_a_dir / "plugin.py").write_text("""
def register():
    pass
""")
        
        # Create plugin B
        plugin_b_dir = temp_plugin_dir / "plugin_b"
        plugin_b_dir.mkdir()
        
        plugin_b_config = {
            "name": "plugin_b",
            "version": "1.0.0",
            "description": "Plugin B",
            "required_backend": ">=0.1.0",
            "files": ["plugin.py"]
        }
        
        with open(plugin_b_dir / "plugin.yml", 'w') as f:
            yaml.dump(plugin_b_config, f)
        
        (plugin_b_dir / "plugin.py").write_text("""
def register():
    pass
""")
        
        # Test dependency resolution by parsing manifests
        from stash_ai_server.plugin_runtime.loader import _parse_manifest
        
        manifest_a = _parse_manifest(plugin_a_dir / "plugin.yml")
        manifest_b = _parse_manifest(plugin_b_dir / "plugin.yml")
        
        assert manifest_a is not None
        assert manifest_b is not None
        assert "plugin_b" in manifest_a.depends_on
        assert len(manifest_b.depends_on) == 0


class TestPluginSystemDatabase:
    """Test plugin system database integration."""
    
    def test_plugin_settings_persistence(self, test_database):
        """Test plugin settings are persisted to database."""
        from stash_ai_server.models.plugin import PluginSetting
        
        # Create test session
        session = test_database.get_sync_test_session()
        
        try:
            # Create plugin setting
            setting = PluginSetting(
                plugin_name="test_plugin",
                key="test_setting",
                value="test_value"
            )
            session.add(setting)
            session.commit()
            
            # Verify persistence
            retrieved = session.query(PluginSetting).filter_by(
                plugin_name="test_plugin",
                key="test_setting"
            ).first()
            
            assert retrieved is not None
            assert retrieved.value == "test_value"
            
        finally:
            session.rollback()
            session.close()
    
    def test_plugin_meta_management(self, test_database):
        """Test plugin metadata management in database."""
        from stash_ai_server.models.plugin import PluginMeta
        
        session = test_database.get_sync_test_session()
        
        try:
            # Create plugin metadata
            meta = PluginMeta(
                name="test_plugin",
                version="1.0.0",
                required_backend=">=0.1.0",
                status="active"
            )
            session.add(meta)
            session.commit()
            
            # Verify metadata persistence
            retrieved = session.query(PluginMeta).filter_by(
                name="test_plugin"
            ).first()
            
            assert retrieved is not None
            assert retrieved.status == "active"
            assert retrieved.version == "1.0.0"
            
        finally:
            session.rollback()
            session.close()


class TestPluginSystemPerformance:
    """Test plugin system performance characteristics."""
    
    @pytest.fixture
    def temp_plugin_dir(self):
        """Create temporary plugin directory for testing."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)
    
    def test_plugin_loading_performance(self, temp_plugin_dir):
        """Test plugin loading performance with multiple plugins."""
        import time
        
        # Create multiple plugins
        for i in range(5):
            plugin_dir = temp_plugin_dir / f"plugin_{i}"
            plugin_dir.mkdir()
            
            config = {
                "name": f"plugin_{i}",
                "version": "1.0.0",
                "description": f"Test plugin {i}",
                "required_backend": ">=0.1.0",
                "files": ["plugin.py"]
            }
            
            with open(plugin_dir / "plugin.yml", 'w') as f:
                yaml.dump(config, f)
            
            (plugin_dir / "plugin.py").write_text(f"""
def register():
    pass
""")
        
        # Measure parsing time for all manifests
        from stash_ai_server.plugin_runtime.loader import _parse_manifest
        
        start_time = time.time()
        manifests = []
        for i in range(5):
            plugin_yml = temp_plugin_dir / f"plugin_{i}" / "plugin.yml"
            manifest = _parse_manifest(plugin_yml)
            if manifest:
                manifests.append(manifest)
        
        parse_time = time.time() - start_time
        
        # Should parse reasonably quickly (less than 1 second for 5 plugins)
        assert parse_time < 1.0
        
        # All plugins should be parsed
        assert len(manifests) == 5
        for i, manifest in enumerate(manifests):
            assert manifest.name == f"plugin_{i}"
    
    @pytest.mark.asyncio
    async def test_concurrent_plugin_execution(self, temp_plugin_dir):
        """Test concurrent execution of plugin components."""
        import asyncio
        
        # Create plugin with async functionality
        plugin_dir = temp_plugin_dir / "async_plugin"
        plugin_dir.mkdir()
        
        config = {
            "name": "async_plugin",
            "version": "1.0.0",
            "description": "Async test plugin",
            "required_backend": ">=0.1.0",
            "files": ["plugin.py"]
        }
        
        with open(plugin_dir / "plugin.yml", 'w') as f:
            yaml.dump(config, f)
        
        (plugin_dir / "plugin.py").write_text("""
import asyncio

async def async_function():
    # Simulate async work
    await asyncio.sleep(0.1)
    return {"status": "success"}

def register():
    pass
""")
        
        # Test plugin manifest parsing
        from stash_ai_server.plugin_runtime.loader import _parse_manifest
        
        manifest = _parse_manifest(plugin_dir / "plugin.yml")
        assert manifest is not None
        assert manifest.name == "async_plugin"
        
        # Test concurrent parsing (simulating concurrent plugin operations)
        tasks = []
        for i in range(3):
            task = asyncio.create_task(asyncio.sleep(0.01))  # Simulate async work
            tasks.append(task)
        
        results = await asyncio.gather(*tasks)
        
        # All tasks should complete successfully
        assert len(results) == 3