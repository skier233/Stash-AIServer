"""Test failure plugin implementation."""

import logging
import os

_log = logging.getLogger(__name__)

# Plugin state for testing
_plugin_state = {
    'registered': False,
    'unregistered': False,
    'register_call_count': 0,
    'unregister_call_count': 0,
    'registration_failed': False,
    'failure_reason': None
}


def register():
    """Register the test failure plugin."""
    global _plugin_state
    _plugin_state['register_call_count'] += 1
    
    # Check if we should simulate a failure
    simulate_failure = os.getenv('TEST_PLUGIN_SIMULATE_FAILURE', 'false').lower() == 'true'
    
    if simulate_failure:
        _plugin_state['registration_failed'] = True
        _plugin_state['failure_reason'] = "Simulated registration failure for testing"
        _log.error("Test failure plugin simulating registration failure")
        raise RuntimeError("Simulated registration failure for testing")
    
    _plugin_state['registered'] = True
    _log.info("Test failure plugin registered successfully")


def unregister():
    """Unregister the test failure plugin."""
    global _plugin_state
    _plugin_state['unregistered'] = True
    _plugin_state['unregister_call_count'] += 1
    
    # Check if we should simulate unregister failure
    simulate_unregister_failure = os.getenv('TEST_PLUGIN_SIMULATE_UNREGISTER_FAILURE', 'false').lower() == 'true'
    
    if simulate_unregister_failure:
        _log.error("Test failure plugin simulating unregister failure")
        raise RuntimeError("Simulated unregister failure for testing")
    
    _log.info("Test failure plugin unregistered successfully")


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
        'registration_failed': False,
        'failure_reason': None
    }


def simulate_failure():
    """Trigger a failure for testing error handling."""
    raise RuntimeError("Simulated plugin failure for testing")


def simulate_async_failure():
    """Trigger an async failure for testing error handling."""
    import asyncio
    
    async def _fail():
        await asyncio.sleep(0.1)
        raise RuntimeError("Simulated async plugin failure for testing")
    
    return _fail()