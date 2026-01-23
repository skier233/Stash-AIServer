"""Test recommender plugin implementation."""

import logging
from typing import List, Dict, Any, Optional
from stash_ai_server.recommendations.registry import recommender_registry

_log = logging.getLogger(__name__)

# Plugin state for testing
_plugin_state = {
    'registered': False,
    'unregistered': False,
    'register_call_count': 0,
    'unregister_call_count': 0,
    'recommender_registered': False,
    'recommendation_calls': 0,
    'last_recommendation_params': None
}


class TestRecommender:
    """Test recommender for testing recommender system functionality."""
    
    def __init__(self):
        self.name = "test_recommender"
        self.description = "Test recommender for plugin testing"
    
    async def get_recommendations(
        self, 
        user_id: str, 
        limit: int = 10, 
        context: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Generate test recommendations."""
        global _plugin_state
        _plugin_state['recommendation_calls'] += 1
        _plugin_state['last_recommendation_params'] = {
            'user_id': user_id,
            'limit': limit,
            'context': context
        }
        
        _log.info(f"Test recommender called for user {user_id}, limit {limit}")
        
        # Return test recommendations
        recommendations = []
        for i in range(min(limit, 3)):  # Return up to 3 test recommendations
            recommendations.append({
                'scene_id': f'test_scene_{i}',
                'score': 0.9 - (i * 0.1),
                'reason': f'Test recommendation {i}',
                'metadata': {
                    'test_plugin': True,
                    'recommendation_index': i
                }
            })
        
        return recommendations


def register():
    """Register the test recommender plugin."""
    global _plugin_state
    _plugin_state['registered'] = True
    _plugin_state['register_call_count'] += 1
    
    # Register the test recommender
    test_recommender = TestRecommender()
    recommender_registry.register(test_recommender.name, test_recommender)
    _plugin_state['recommender_registered'] = True
    
    _log.info("Test recommender plugin registered")


def unregister():
    """Unregister the test recommender plugin."""
    global _plugin_state
    _plugin_state['unregistered'] = True
    _plugin_state['unregister_call_count'] += 1
    
    # Unregister the test recommender
    try:
        recommender_registry.unregister("test_recommender")
        _plugin_state['recommender_registered'] = False
    except Exception as e:
        _log.warning(f"Failed to unregister test recommender: {e}")
    
    _log.info("Test recommender plugin unregistered")


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
        'recommender_registered': False,
        'recommendation_calls': 0,
        'last_recommendation_params': None
    }