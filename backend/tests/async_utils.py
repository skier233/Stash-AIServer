"""Async testing utilities for WebSocket and task management testing."""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import websockets
from fastapi import FastAPI
from fastapi.testclient import TestClient


class AsyncTestClient:
    """Wrapper for FastAPI TestClient with async capabilities."""
    
    def __init__(self, app: FastAPI):
        self.app = app
        self.client = TestClient(app)
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if hasattr(self.client, '__exit__'):
                self.client.__exit__(exc_type, exc_val, exc_tb)
            elif hasattr(self.client, 'close'):
                self.client.close()
        except AttributeError:
            # Some versions of TestClient may not have proper context manager support
            pass
    
    async def websocket_connect(self, url: str, timeout: float = 5.0):
        """Connect to WebSocket endpoint for testing."""
        return AsyncWebSocketTestSession(self.client, url, timeout)
    
    async def async_request(self, method: str, url: str, timeout: float = 30.0, **kwargs):
        """Make async HTTP request (runs sync request in thread pool)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            lambda: getattr(self.client, method.lower())(url, **kwargs)
        )
    
    def get(self, *args, **kwargs):
        return self.client.get(*args, **kwargs)
    
    def post(self, *args, **kwargs):
        return self.client.post(*args, **kwargs)
    
    def put(self, *args, **kwargs):
        return self.client.put(*args, **kwargs)
    
    def delete(self, *args, **kwargs):
        return self.client.delete(*args, **kwargs)


class AsyncWebSocketTestSession:
    """Async WebSocket test session."""
    
    def __init__(self, client: TestClient, url: str, timeout: float = 5.0):
        self.client = client
        self.url = url
        self.timeout = timeout
        self.websocket = None
        self.messages: List[Dict[str, Any]] = []
        self.closed = False
    
    async def __aenter__(self):
        # Use TestClient's websocket_connect in a thread
        loop = asyncio.get_event_loop()
        self.websocket = await loop.run_in_executor(
            None,
            lambda: self.client.websocket_connect(self.url)
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.websocket and not self.closed:
            await self.close()
    
    async def send_json(self, data: Dict[str, Any]):
        """Send JSON message to WebSocket."""
        if self.websocket:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.websocket.send_json(data)
            )
    
    async def receive_json(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Receive JSON message from WebSocket."""
        if not self.websocket:
            raise RuntimeError("WebSocket not connected")
        
        timeout = timeout or self.timeout
        loop = asyncio.get_event_loop()
        
        try:
            message = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: self.websocket.receive_json()
                ),
                timeout=timeout
            )
            self.messages.append(message)
            return message
        except asyncio.TimeoutError:
            raise TimeoutError(f"No WebSocket message received within {timeout} seconds")
    
    async def close(self):
        """Close WebSocket connection."""
        if self.websocket and not self.closed:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.websocket.close()
            )
            self.closed = True


class TaskManagerTestUtils:
    """Utilities for testing task manager functionality."""
    
    @staticmethod
    async def wait_for_task_completion(
        task_id: str, 
        timeout: float = 10.0,
        expected_status: Optional[str] = None
    ) -> Dict[str, Any]:
        """Wait for task completion with timeout."""
        # Lazy import to avoid initialization issues
        from stash_ai_server.tasks.manager import manager
        from stash_ai_server.tasks.models import TaskStatus
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Get task status from manager
            task_info = manager.get_task_info(task_id)
            
            if not task_info:
                await asyncio.sleep(0.1)
                continue
            
            status = task_info.get('status')
            
            # Check if task is in a terminal state
            if status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                if expected_status and status != expected_status:
                    raise AssertionError(f"Task {task_id} ended with status {status}, expected {expected_status}")
                return task_info
            
            await asyncio.sleep(0.1)
        
        # Timeout reached
        final_info = manager.get_task_info(task_id)
        raise TimeoutError(
            f"Task {task_id} did not complete within {timeout} seconds. "
            f"Final status: {final_info.get('status') if final_info else 'unknown'}"
        )
    
    @staticmethod
    async def assert_task_status(task_id: str, expected_status: str, timeout: float = 5.0):
        """Assert task reaches expected status within timeout."""
        # Lazy import to avoid initialization issues
        from stash_ai_server.tasks.manager import manager
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            task_info = manager.get_task_info(task_id)
            
            if task_info and task_info.get('status') == expected_status:
                return task_info
            
            await asyncio.sleep(0.1)
        
        # Timeout reached
        final_info = manager.get_task_info(task_id)
        final_status = final_info.get('status') if final_info else 'unknown'
        raise AssertionError(
            f"Task {task_id} did not reach status {expected_status} within {timeout} seconds. "
            f"Final status: {final_status}"
        )
    
    @staticmethod
    async def wait_for_task_count(expected_count: int, timeout: float = 5.0):
        """Wait for specific number of active tasks."""
        # Lazy import to avoid initialization issues
        from stash_ai_server.tasks.manager import manager
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            active_tasks = manager.get_active_tasks()
            if len(active_tasks) == expected_count:
                return active_tasks
            
            await asyncio.sleep(0.1)
        
        active_tasks = manager.get_active_tasks()
        raise TimeoutError(
            f"Expected {expected_count} active tasks, but found {len(active_tasks)} "
            f"after {timeout} seconds"
        )
    
    @staticmethod
    def get_task_history(limit: int = 100) -> List[Dict[str, Any]]:
        """Get task execution history."""
        # Lazy import to avoid initialization issues
        from stash_ai_server.tasks.manager import manager
        
        return manager.get_task_history(limit=limit)
    
    @staticmethod
    def clear_task_history():
        """Clear task execution history."""
        # Lazy import to avoid initialization issues
        from stash_ai_server.tasks.manager import manager
        
        if hasattr(manager, 'clear_history'):
            manager.clear_history()


@asynccontextmanager
async def temporary_task_service(service_name: str, handler_func, concurrency_limit: int = 1):
    """Temporarily register a task service for testing."""
    # Lazy import to avoid initialization issues
    from stash_ai_server.services.registry import services
    
    # Register temporary service
    services.register_service(
        service_name=service_name,
        handler=handler_func,
        concurrency_limit=concurrency_limit
    )
    
    try:
        yield service_name
    finally:
        # Unregister service
        services.unregister_service(service_name)


class MockWebSocketManager:
    """Mock WebSocket manager for testing WebSocket functionality."""
    
    def __init__(self):
        self.connections: List[AsyncMock] = []
        self.sent_messages: List[Dict[str, Any]] = []
    
    async def connect(self, websocket: AsyncMock):
        """Mock WebSocket connection."""
        self.connections.append(websocket)
    
    async def disconnect(self, websocket: AsyncMock):
        """Mock WebSocket disconnection."""
        if websocket in self.connections:
            self.connections.remove(websocket)
    
    async def send_message(self, message: Dict[str, Any], websocket: Optional[AsyncMock] = None):
        """Mock sending message to WebSocket(s)."""
        self.sent_messages.append(message)
        
        if websocket:
            await websocket.send_json(message)
        else:
            # Send to all connections
            for conn in self.connections:
                await conn.send_json(message)
    
    def get_sent_messages(self) -> List[Dict[str, Any]]:
        """Get all sent messages."""
        return self.sent_messages.copy()
    
    def clear_sent_messages(self):
        """Clear sent messages history."""
        self.sent_messages.clear()
    
    def get_connection_count(self) -> int:
        """Get number of active connections."""
        return len(self.connections)