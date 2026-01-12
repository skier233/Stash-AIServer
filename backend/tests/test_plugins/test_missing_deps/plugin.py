"""Test missing dependencies plugin implementation."""

import logging

_log = logging.getLogger(__name__)

# Plugin state for testing
_plugin_state = {
    'registered': False,
    'unregistered': False,
    'register_call_count': 0,
    'unregister_call_count': 0,
    'missing_dependencies': ['nonexistent_plugin', 'another_missing_plugin']
}


def register():
    """Register the test missing dependencies plugin."""
    global _plugin_state
    _plugin_state['registered'] = True
    _plugin_state['register_call_count'] += 1
    
    # This plugin should not actually register due to missing dependencies
    # But if it does get called, we'll track it for testing
    _log.info("Test missing dependencies plugin register() called (should not happen)")


def unregister():
    """Unregister the test missing dependencies plugin."""
    global _plugin_state
    _plugin_state['unregistered'] = True
    _plugin_state['unregister_call_count'] += 1
    
    _log.info("Test missing dependencies plugin unregistered")


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
        'missing_dependencies': ['nonexistent_plugin', 'another_missing_plugin']
    }