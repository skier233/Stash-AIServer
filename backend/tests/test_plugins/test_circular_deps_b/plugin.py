"""Test circular dependencies B plugin implementation."""

import logging

_log = logging.getLogger(__name__)

# Plugin state for testing
_plugin_state = {
    'registered': False,
    'unregistered': False,
    'register_call_count': 0,
    'unregister_call_count': 0,
    'circular_dependency_detected': False
}


def register():
    """Register the test circular dependencies B plugin."""
    global _plugin_state
    _plugin_state['registered'] = True
    _plugin_state['register_call_count'] += 1
    
    # Try to check circular dependency
    try:
        from stash_ai_server.plugins.test_circular_deps_a.plugin import get_plugin_state
        a_state = get_plugin_state()
        _plugin_state['circular_dependency_detected'] = True
        _log.info(f"Circular dependency B registered, A state: {a_state}")
    except ImportError:
        _log.info("Circular dependency B registered, A not available")
    
    _log.info("Test circular dependencies B plugin registered")


def unregister():
    """Unregister the test circular dependencies B plugin."""
    global _plugin_state
    _plugin_state['unregistered'] = True
    _plugin_state['unregister_call_count'] += 1
    
    _log.info("Test circular dependencies B plugin unregistered")


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
        'circular_dependency_detected': False
    }