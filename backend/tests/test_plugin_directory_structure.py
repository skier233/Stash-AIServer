"""Test the test plugin directory structure and loading override system."""

import pytest
import os
from pathlib import Path

from tests.test_plugins import (
    test_plugin_loader,
    isolated_test_plugins,
    reset_all_test_plugin_states,
    get_test_plugin_state
)


def test_test_plugin_directory_exists():
    """Test that the test plugin directory exists and contains expected plugins."""
    test_plugins = test_plugin_loader.get_available_test_plugins()
    
    expected_plugins = {
        'base_test_plugin',
        'test_recommender', 
        'test_service',
        'test_async_tasks',
        'test_dependencies',
        'test_circular_deps_a',
        'test_circular_deps_b',
        'test_missing_deps',
        'test_failure_plugin'
    }
    
    assert set(test_plugins) == expected_plugins, f"Expected {expected_plugins}, got {set(test_plugins)}"


def test_plugin_directory_override():
    """Test that plugin directory override works correctly."""
    original_env = os.environ.get('AI_SERVER_PLUGINS_DIR')
    
    with test_plugin_loader.override_plugin_directory():
        # Check that environment variable is set
        assert os.environ.get('AI_SERVER_PLUGINS_DIR') == str(test_plugin_loader.test_plugin_dir)
    
    # Check that environment is restored
    if original_env:
        assert os.environ.get('AI_SERVER_PLUGINS_DIR') == original_env
    else:
        assert 'AI_SERVER_PLUGINS_DIR' not in os.environ


def test_isolated_plugin_environment():
    """Test that isolated plugin environments work correctly."""
    selected_plugins = ['base_test_plugin', 'test_recommender']
    
    with isolated_test_plugins(selected_plugins) as temp_dir:
        # Check that temporary directory exists
        assert temp_dir.exists()
        
        # Check that only selected plugins are present
        plugin_dirs = [d.name for d in temp_dir.iterdir() if d.is_dir()]
        assert set(plugin_dirs) == set(selected_plugins)
        
        # Check that each plugin has required files
        for plugin_name in selected_plugins:
            plugin_dir = temp_dir / plugin_name
            assert (plugin_dir / 'plugin.yml').exists()
            assert (plugin_dir / 'plugin.py').exists()
    
    # Check that temporary directory is cleaned up
    assert not temp_dir.exists()


def test_plugin_manifest_structure():
    """Test that all test plugins have valid manifest structure."""
    import yaml
    
    test_plugins = test_plugin_loader.get_available_test_plugins()
    
    for plugin_name in test_plugins:
        plugin_dir = test_plugin_loader.test_plugin_dir / plugin_name
        manifest_path = plugin_dir / 'plugin.yml'
        
        assert manifest_path.exists(), f"Plugin {plugin_name} missing plugin.yml"
        
        # Load and validate manifest
        with open(manifest_path, 'r') as f:
            manifest = yaml.safe_load(f)
        
        # Check required fields
        assert manifest.get('name') == plugin_name, f"Plugin {plugin_name} name mismatch"
        assert 'version' in manifest, f"Plugin {plugin_name} missing version"
        assert 'required_backend' in manifest, f"Plugin {plugin_name} missing required_backend"
        assert 'files' in manifest, f"Plugin {plugin_name} missing files"
        assert 'depends_on' in manifest, f"Plugin {plugin_name} missing depends_on"


def test_plugin_implementation_structure():
    """Test that all test plugins have valid implementation structure."""
    import importlib.util
    
    test_plugins = test_plugin_loader.get_available_test_plugins()
    
    for plugin_name in test_plugins:
        plugin_dir = test_plugin_loader.test_plugin_dir / plugin_name
        plugin_py_path = plugin_dir / 'plugin.py'
        
        assert plugin_py_path.exists(), f"Plugin {plugin_name} missing plugin.py"
        
        # Load module to check structure
        spec = importlib.util.spec_from_file_location(f"{plugin_name}.plugin", plugin_py_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Check required functions
        assert hasattr(module, 'register'), f"Plugin {plugin_name} missing register() function"
        assert hasattr(module, 'unregister'), f"Plugin {plugin_name} missing unregister() function"
        assert hasattr(module, 'get_plugin_state'), f"Plugin {plugin_name} missing get_plugin_state() function"
        assert hasattr(module, 'reset_plugin_state'), f"Plugin {plugin_name} missing reset_plugin_state() function"
        
        # Check that functions are callable
        assert callable(module.register), f"Plugin {plugin_name} register() not callable"
        assert callable(module.unregister), f"Plugin {plugin_name} unregister() not callable"
        assert callable(module.get_plugin_state), f"Plugin {plugin_name} get_plugin_state() not callable"
        assert callable(module.reset_plugin_state), f"Plugin {plugin_name} reset_plugin_state() not callable"


def test_dependency_relationships():
    """Test that plugin dependency relationships are correctly defined."""
    import yaml
    
    # Load all manifests
    manifests = {}
    for plugin_name in test_plugin_loader.get_available_test_plugins():
        plugin_dir = test_plugin_loader.test_plugin_dir / plugin_name
        manifest_path = plugin_dir / 'plugin.yml'
        
        with open(manifest_path, 'r') as f:
            manifests[plugin_name] = yaml.safe_load(f)
    
    # Check specific dependency relationships
    
    # test_dependencies should depend on base_test_plugin
    assert 'base_test_plugin' in manifests['test_dependencies']['depends_on']
    
    # Circular dependencies should depend on each other
    assert 'test_circular_deps_b' in manifests['test_circular_deps_a']['depends_on']
    assert 'test_circular_deps_a' in manifests['test_circular_deps_b']['depends_on']
    
    # Missing deps should depend on non-existent plugins
    missing_deps = manifests['test_missing_deps']['depends_on']
    assert 'nonexistent_plugin' in missing_deps
    assert 'another_missing_plugin' in missing_deps
    
    # Verify non-existent dependencies don't exist
    available_plugins = set(manifests.keys())
    for dep in missing_deps:
        assert dep not in available_plugins, f"Dependency {dep} should not exist but was found"


if __name__ == "__main__":
    pytest.main([__file__])