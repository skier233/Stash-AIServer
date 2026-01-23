"""Base test plugin implementation."""

import logging

_log = logging.getLogger(__name__)

# Plugin state for testing
_plugin_state = {
    'registered': False,
    'unregistered': False,
    'register_call_count': 0,
    'unregister_call_count': 0
}


def register():
    """Register the test plugin."""
    global _plugin_state
    _plugin_state['registered'] = True
    _plugin_state['register_call_count'] += 1
    _log.info("Base test plugin registered")


def unregister():
    """Unregister the test plugin."""
    global _plugin_state
    _plugin_state['unregistered'] = True
    _plugin_state['unregister_call_count'] += 1
    _log.info("Base test plugin unregistered")


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
        'unregister_call_count': 0
    }