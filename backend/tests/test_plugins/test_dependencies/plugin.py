"""Test dependencies plugin implementation."""

import logging

_log = logging.getLogger(__name__)

# Plugin state for testing
_plugin_state = {
    'registered': False,
    'unregistered': False,
    'register_call_count': 0,
    'unregister_call_count': 0,
    'dependency_check_passed': False,
    'base_plugin_state': None
}


def register():
    """Register the test dependencies plugin."""
    global _plugin_state
    _plugin_state['registered'] = True
    _plugin_state['register_call_count'] += 1
    
    # Check if base dependency is available
    try:
        from stash_ai_server.plugins.base_test_plugin.plugin import get_plugin_state
        base_state = get_plugin_state()
        _plugin_state['base_plugin_state'] = base_state
        _plugin_state['dependency_check_passed'] = base_state.get('registered', False)
        
        _log.info(f"Test dependencies plugin registered, base plugin registered: {base_state.get('registered', False)}")
    except ImportError as e:
        _log.error(f"Failed to import base test plugin: {e}")
        _plugin_state['dependency_check_passed'] = False
    
    _log.info("Test dependencies plugin registered")


def unregister():
    """Unregister the test dependencies plugin."""
    global _plugin_state
    _plugin_state['unregistered'] = True
    _plugin_state['unregister_call_count'] += 1
    
    _log.info("Test dependencies plugin unregistered")


def get_plugin_state():
    """Get current plugin state for testing."""
    return _plugin_state.copy()


def reset_plugin_state():
    """Reset plugin state for testing."""
    global _plugin_state
    _plugin_state = {
        'registered': False,
        'unregistered': False,
        'register_call_count': 0,
        'unregister_call_count': 0,
        'dependency_check_passed': False,
        'base_plugin_state': None
    }