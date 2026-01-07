"""Test async utilities without database dependencies."""

import pytest
import pytest_asyncio
import asyncio
from unittest.mock import AsyncMock, MagicMock

from tests.async_utils import (
    AsyncTestClient, 
    AsyncWebSocketTestSession, 
    TaskManagerTestUtils,
    MockWebSocketManager,
    temporary_task_service
)


class TestAsyncUtils:
    """Test async utilities functionality."""
    
    def test_async_test_client_creation(self):
        """Test AsyncTestClient can be created."""
        # Create a mock FastAPI app
        mock_app = MagicMock()
        
        client = AsyncTestClient(mock_app)
        assert client.app == mock_app
        assert client.client is not None
    
    def test_async_websocket_session_creation(self):
        """Test AsyncWebSocketTestSession can be created."""
        mock_client = MagicMock()
        url = "/ws/test"
        timeout = 5.0
        
        session = AsyncWebSocketTestSession(mock_client, url, timeout)
        assert session.client == mock_client
        assert session.url == url
        assert session.timeout == timeout
        assert session.messages == []
        assert session.closed is False
    
    def test_task_manager_test_utils(self):
        """Test TaskManagerTestUtils methods exist."""
        utils = TaskManagerTestUtils()
        
        # Test that all expected methods exist
        assert hasattr(utils, 'wait_for_task_completion')
        assert hasattr(utils, 'assert_task_status')
        assert hasattr(utils, 'wait_for_task_count')
        assert hasattr(utils, 'get_task_history')
        assert hasattr(utils, 'clear_task_history')
        
        # Test static methods can be called
        assert callable(TaskManagerTestUtils.wait_for_task_completion)
        assert callable(TaskManagerTestUtils.assert_task_status)
        assert callable(TaskManagerTestUtils.wait_for_task_count)
        assert callable(TaskManagerTestUtils.get_task_history)
        assert callable(TaskManagerTestUtils.clear_task_history)
    
    def test_mock_websocket_manager(self):
        """Test MockWebSocketManager functionality."""
        manager = MockWebSocketManager()
        
        # Test initial state
        assert manager.get_connection_count() == 0
        assert manager.get_sent_messages() == []
        
        # Test adding connections
        mock_ws1 = AsyncMock()
        mock_ws2 = AsyncMock()
        
        asyncio.run(manager.connect(mock_ws1))
        assert manager.get_connection_count() == 1
        
        asyncio.run(manager.connect(mock_ws2))
        assert manager.get_connection_count() == 2
        
        # Test sending messages
        test_message = {"type": "test", "data": "hello"}
        asyncio.run(manager.send_message(test_message))
        
        sent_messages = manager.get_sent_messages()
        assert len(sent_messages) == 1
        assert sent_messages[0] == test_message
        
        # Test disconnecting
        asyncio.run(manager.disconnect(mock_ws1))
        assert manager.get_connection_count() == 1
        
        # Test clearing messages
        manager.clear_sent_messages()
        assert manager.get_sent_messages() == []
    
    @pytest_asyncio.fixture
    async def mock_service_handler(self):
        """Mock service handler for testing."""
        async def handler(context, params):
            return {"status": "success", "result": "test"}
        return handler
    
    @pytest.mark.asyncio
    async def test_temporary_task_service(self, mock_service_handler):
        """Test temporary task service context manager."""
        service_name = "test_service"
        
        # Mock the services registry
        from unittest.mock import patch
        
        with patch('stash_ai_server.services.registry.services') as mock_services:
            async with temporary_task_service(service_name, mock_service_handler, 2) as registered_name:
                assert registered_name == service_name
                
                # Verify service was registered
                mock_services.register_service.assert_called_once_with(
                    service_name=service_name,
                    handler=mock_service_handler,
                    concurrency_limit=2
                )
            
            # Verify service was unregistered
            mock_services.unregister_service.assert_called_once_with(service_name)
    
    def test_async_test_client_context_manager(self):
        """Test AsyncTestClient context manager functionality."""
        mock_app = MagicMock()
        
        with AsyncTestClient(mock_app) as client:
            assert client.app == mock_app
            assert client.client is not None
        
        # Context manager should exit cleanly
    
    def test_websocket_session_message_tracking(self):
        """Test WebSocket session message tracking."""
        mock_client = MagicMock()
        session = AsyncWebSocketTestSession(mock_client, "/ws/test", 5.0)
        
        # Test initial state
        assert len(session.messages) == 0
        
        # Simulate adding messages (would normally be done by receive_json)
        test_message = {"type": "test", "data": "hello"}
        session.messages.append(test_message)
        
        assert len(session.messages) == 1
        assert session.messages[0] == test_message