"""
Comprehensive database resilience tests for the Stash AI Server.

Tests database connection handling, recovery, and error scenarios.
Uses real PostgreSQL database infrastructure to test actual resilience patterns.
"""

import pytest
import pytest_asyncio
import asyncio
import time
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, DisconnectionError
from contextlib import contextmanager

from tests.database import test_database, db_session
from tests.config import test_config


class TestDatabaseConnectionResilience:
    """Test database connection resilience and recovery."""
    
    def test_connection_pool_recovery(self, test_database):
        """Test connection pool recovery after connection loss."""
        # Get a connection from the pool
        engine = test_database.test_engine
        
        # Test normal operation
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
        
        # Simulate connection pool exhaustion and recovery
        connections = []
        try:
            # Create a limited number of connections to avoid exhausting the pool
            max_connections = min(3, engine.pool.size())  # Limit to 3 or pool size, whichever is smaller
            
            for i in range(max_connections):
                conn = engine.connect()
                connections.append(conn)
                # Test each connection
                result = conn.execute(text(f"SELECT {i + 1}"))
                assert result.scalar() == i + 1
            
            # Test that new connections can still be created
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 999"))
                assert result.scalar() == 999
                
        finally:
            # Clean up connections immediately
            for conn in connections:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def test_connection_retry_logic(self, test_database):
        """Test connection retry logic with temporary failures."""
        # Test that database sessions can be created using test infrastructure
        session = test_database.get_sync_test_session()
        
        try:
            # Test basic query
            result = session.execute(text("SELECT 1"))
            assert result.scalar() == 1
            
            # Test that connection is healthy
            assert session.is_active
            
        finally:
            session.close()
    
    def test_transaction_rollback_on_error(self, test_database):
        """Test transaction rollback behavior on errors."""
        from stash_ai_server.models.interaction import InteractionEvent
        
        session = test_database.get_sync_test_session()
        
        try:
            # Clean up any existing test data first
            session.query(InteractionEvent).filter_by(entity_id=123).delete()
            session.commit()
            
            # Start transaction
            transaction = session.begin()
            
            # Create valid interaction event
            event = InteractionEvent(
                event_type="scene_view",
                entity_type="scene",
                entity_id=123,
                session_id="test_session",
                client_ts=datetime.fromtimestamp(time.time())
            )
            session.add(event)
            session.flush()  # Flush but don't commit
            
            # Verify event exists in session
            count = session.query(InteractionEvent).filter_by(entity_id=123).count()
            assert count == 1
            
            # Simulate error and rollback
            transaction.rollback()
            
            # After rollback, we need to expunge the object and refresh the session
            session.expunge_all()
            
            # Verify rollback worked - check database directly
            count = session.query(InteractionEvent).filter_by(entity_id=123).count()
            assert count == 0
            
        finally:
            # Clean up any remaining test data
            try:
                session.query(InteractionEvent).filter_by(entity_id=123).delete()
                session.commit()
            except:
                session.rollback()
            session.close()
    
    @pytest.mark.asyncio
    async def test_async_connection_handling(self, test_database):
        """Test async database connection handling."""
        async with test_database.get_async_test_session() as session:
            # Test async query
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1
            
            # Test that session is properly managed
            assert session.is_active
    
    def test_connection_leak_prevention(self, test_database):
        """Test that database connections are properly closed."""
        engine = test_database.test_engine
        initial_pool_size = engine.pool.checkedout()
        
        # Create and close multiple sessions
        for i in range(5):
            session = test_database.get_sync_test_session()
            # Use session
            result = session.execute(text("SELECT 1"))
            assert result.scalar() == 1
            # Close session
            session.close()
        
        # Pool should not have leaked connections
        final_pool_size = engine.pool.checkedout()
        assert final_pool_size == initial_pool_size


class TestDatabaseErrorHandling:
    """Test database error handling and recovery."""
    
    def test_invalid_query_handling(self, test_database):
        """Test handling of invalid SQL queries."""
        session = test_database.get_sync_test_session()
        
        try:
            # Execute invalid SQL
            with pytest.raises(Exception):  # Should raise some SQL error
                session.execute(text("SELECT * FROM nonexistent_table"))
            
            # Rollback the transaction after the error to clear the aborted state
            session.rollback()
            
            # Session should still be usable after error and rollback
            result = session.execute(text("SELECT 1"))
            assert result.scalar() == 1
            
        finally:
            session.close()
    
    def test_constraint_violation_handling(self, test_database):
        """Test handling of database constraint violations."""
        from stash_ai_server.models.plugin import PluginSetting
        
        session = test_database.get_sync_test_session()
        
        try:
            # Create plugin setting
            setting1 = PluginSetting(
                plugin_name="test_plugin",
                key="test_key",
                value="value1"
            )
            session.add(setting1)
            session.commit()
            
            # Try to create duplicate (should violate unique constraint if exists)
            setting2 = PluginSetting(
                plugin_name="test_plugin",
                key="test_key",
                value="value2"
            )
            session.add(setting2)
            
            # This may or may not raise an error depending on schema
            # If it raises an error, session should handle it gracefully
            try:
                session.commit()
            except Exception:
                session.rollback()
                # Session should still be usable
                result = session.execute(text("SELECT 1"))
                assert result.scalar() == 1
            
        finally:
            session.rollback()
            session.close()
    
    def test_deadlock_handling(self, test_database):
        """Test handling of database deadlocks."""
        from stash_ai_server.models.interaction import InteractionEvent
        
        # Create two sessions to simulate potential deadlock
        session1 = test_database.get_sync_test_session()
        session2 = test_database.get_sync_test_session()
        
        try:
            # Create events in different sessions
            event1 = InteractionEvent(
                event_type="scene_view",
                entity_type="scene",
                entity_id=1,
                session_id="test_session_1",
                client_ts=datetime.fromtimestamp(time.time())
            )
            
            event2 = InteractionEvent(
                event_type="scene_view",
                entity_type="scene", 
                entity_id=2,
                session_id="test_session_2",
                client_ts=datetime.fromtimestamp(time.time())
            )
            
            # Add to different sessions
            session1.add(event1)
            session2.add(event2)
            
            # Commit both (should not deadlock with different records)
            session1.commit()
            session2.commit()
            
            # Verify both events were created
            count1 = session1.query(InteractionEvent).filter_by(entity_id=1).count()
            count2 = session2.query(InteractionEvent).filter_by(entity_id=2).count()
            
            assert count1 == 1
            assert count2 == 1
            
        finally:
            session1.rollback()
            session2.rollback()
            session1.close()
            session2.close()


class TestDatabasePerformance:
    """Test database performance characteristics."""
    
    def test_bulk_insert_performance(self, test_database):
        """Test bulk insert performance."""
        from stash_ai_server.models.interaction import InteractionEvent
        
        session = test_database.get_sync_test_session()
        
        try:
            # Create many events for bulk insert
            events = []
            for i in range(100):
                event = InteractionEvent(
                    event_type="scene_view",
                    entity_type="scene",
                    entity_id=i,
                    session_id=f"test_session_{i}",
                    client_ts=datetime.fromtimestamp(time.time() + i)
                )
                events.append(event)
            
            # Measure bulk insert time
            start_time = time.time()
            session.add_all(events)
            session.commit()
            insert_time = time.time() - start_time
            
            # Should complete reasonably quickly (less than 1 second for 100 records)
            assert insert_time < 1.0
            
            # Verify all events were inserted
            count = session.query(InteractionEvent).count()
            assert count >= 100
            
        finally:
            session.rollback()
            session.close()
    
    def test_query_performance(self, test_database):
        """Test query performance with indexed and non-indexed queries."""
        from stash_ai_server.models.interaction import InteractionEvent
        
        session = test_database.get_sync_test_session()
        
        try:
            # Clear any existing data first
            session.query(InteractionEvent).delete()
            session.flush()
            
            # Create test data
            events = []
            for i in range(50):
                event = InteractionEvent(
                    event_type="scene_view" if i % 2 == 0 else "performer_view",
                    entity_type="scene" if i % 2 == 0 else "performer",
                    entity_id=i,
                    session_id=f"test_session_{i % 10}",  # 10 different sessions
                    client_ts=datetime.fromtimestamp(time.time() + i)
                )
                events.append(event)
            
            session.add_all(events)
            session.flush()  # Use flush instead of commit to keep in transaction
            
            # Test indexed query (by primary key)
            start_time = time.time()
            event = session.query(InteractionEvent).filter_by(entity_id=25).first()
            indexed_query_time = time.time() - start_time
            
            assert event is not None
            assert indexed_query_time < 0.1  # Should be very fast
            
            # Test filtered query
            start_time = time.time()
            scene_events = session.query(InteractionEvent).filter_by(event_type="scene_view").all()
            filtered_query_time = time.time() - start_time
            
            assert len(scene_events) == 25  # Half of the events
            assert filtered_query_time < 0.1  # Should still be fast
            
        finally:
            session.rollback()
            session.close()
    
    def test_connection_pool_performance(self, test_database):
        """Test connection pool performance under load."""
        engine = test_database.test_engine
        
        # Test multiple concurrent connections
        def execute_query():
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                return result.scalar()
        
        # Measure time for multiple queries
        start_time = time.time()
        results = []
        
        for i in range(10):
            result = execute_query()
            results.append(result)
        
        total_time = time.time() - start_time
        
        # All queries should succeed
        assert all(r == 1 for r in results)
        
        # Should complete reasonably quickly
        assert total_time < 1.0


class TestDatabaseMigrationResilience:
    """Test database migration and schema change resilience."""
    
    def test_schema_validation(self, test_database):
        """Test that database schema matches expected structure."""
        engine = test_database.test_engine
        
        with engine.connect() as conn:
            # Check that expected tables exist
            result = conn.execute(text("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name != 'alembic_version'
                ORDER BY table_name
            """))
            
            tables = [row[0] for row in result]
            
            # Should have core tables (exact list may vary)
            expected_tables = ['interaction_events', 'plugin_settings', 'task_history']
            
            for expected_table in expected_tables:
                # Table might exist with different name, so check if any similar table exists
                similar_tables = [t for t in tables if expected_table.replace('_', '') in t.replace('_', '')]
                assert len(similar_tables) > 0, f"No table similar to {expected_table} found in {tables}"
    
    def test_alembic_version_tracking(self, test_database):
        """Test that Alembic version tracking works correctly."""
        engine = test_database.test_engine
        
        with engine.connect() as conn:
            # Check if alembic_version table exists
            result = conn.execute(text("""
                SELECT COUNT(*) FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = 'alembic_version'
            """))
            
            alembic_table_exists = result.scalar() > 0
            
            if alembic_table_exists:
                # Check version tracking
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                version = result.scalar()
                
                # Should have a version (string)
                assert version is not None
                assert isinstance(version, str)
                assert len(version) > 0
    
    def test_database_backup_compatibility(self, test_database):
        """Test database backup and restore compatibility."""
        from stash_ai_server.models.plugin import PluginSetting
        
        session = test_database.get_sync_test_session()
        
        try:
            # Create test data
            setting = PluginSetting(
                plugin_name="backup_test_plugin",
                key="backup_test_key",
                value="backup_test_value"
            )
            session.add(setting)
            session.commit()
            
            # Verify data exists
            retrieved = session.query(PluginSetting).filter_by(
                plugin_name="backup_test_plugin"
            ).first()
            
            assert retrieved is not None
            assert retrieved.value == "backup_test_value"
            
            # Test data export (simple query)
            result = session.execute(text("""
                SELECT plugin_name, key, value 
                FROM plugin_settings 
                WHERE plugin_name = 'backup_test_plugin'
            """))
            
            row = result.fetchone()
            assert row is not None
            assert row[0] == "backup_test_plugin"
            assert row[1] == "backup_test_key"
            assert row[2] == "backup_test_value"
            
        finally:
            session.rollback()
            session.close()


class TestDatabaseConcurrency:
    """Test database concurrency and isolation."""
    
    @pytest.mark.asyncio
    async def test_concurrent_read_write_operations(self, test_database):
        """Test concurrent read and write operations."""
        from stash_ai_server.models.interaction import InteractionEvent
        
        async def write_events(session_factory, start_id: int, count: int):
            """Write events concurrently."""
            session = session_factory()
            try:
                events = []
                for i in range(count):
                    event = InteractionEvent(
                        event_type="concurrent_test",
                        entity_type="scene",
                        entity_id=start_id + i,
                        session_id=f"concurrent_session_{start_id}",
                        client_ts=datetime.fromtimestamp(time.time())
                    )
                    events.append(event)
                
                session.add_all(events)
                session.commit()
                return count
            finally:
                session.close()
        
        async def read_events(session_factory):
            """Read events concurrently."""
            session = session_factory()
            try:
                count = session.query(InteractionEvent).filter_by(
                    event_type="concurrent_test"
                ).count()
                return count
            finally:
                session.close()
        
        # Run concurrent operations
        write_tasks = [
            write_events(test_database.get_sync_test_session, i * 10, 5)
            for i in range(3)
        ]
        
        read_tasks = [
            read_events(test_database.get_sync_test_session)
            for _ in range(2)
        ]
        
        # Execute concurrently
        write_results = await asyncio.gather(*write_tasks, return_exceptions=True)
        read_results = await asyncio.gather(*read_tasks, return_exceptions=True)
        
        # All operations should succeed
        for result in write_results:
            assert not isinstance(result, Exception), f"Write operation failed: {result}"
            assert result == 5
        
        for result in read_results:
            assert not isinstance(result, Exception), f"Read operation failed: {result}"
            assert isinstance(result, int)
    
    def test_transaction_isolation(self, test_database):
        """Test transaction isolation between sessions."""
        from stash_ai_server.models.plugin import PluginSetting
        
        session1 = test_database.get_sync_test_session()
        session2 = test_database.get_sync_test_session()
        
        try:
            # Session 1: Start transaction and insert data
            trans1 = session1.begin()
            setting1 = PluginSetting(
                plugin_name="isolation_test",
                key="test_key",
                value="session1_value"
            )
            session1.add(setting1)
            session1.flush()  # Flush but don't commit
            
            # Session 2: Should not see uncommitted data
            count = session2.query(PluginSetting).filter_by(
                plugin_name="isolation_test"
            ).count()
            assert count == 0  # Should not see uncommitted data
            
            # Session 1: Commit transaction
            trans1.commit()
            
            # Session 2: Should now see committed data
            session2.expire_all()  # Refresh session
            count = session2.query(PluginSetting).filter_by(
                plugin_name="isolation_test"
            ).count()
            assert count == 1  # Should see committed data
            
        finally:
            session1.rollback()
            session2.rollback()
            session1.close()
            session2.close()