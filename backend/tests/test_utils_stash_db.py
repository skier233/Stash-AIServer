"""
Tests for Stash database utilities.

Tests the Stash database connection and table reflection functionality.
Uses mocked database paths and engines to avoid requiring actual Stash database.
"""

import pytest
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from stash_ai_server.utils.stash_db import (
    _prefer_configured_path,
    _resolve_db_path,
    get_stash_db_path,
    _dispose_locked,
    _build_engine_for_path,
    get_stash_engine,
    get_stash_sessionmaker,
    stash_db_session,
    get_stash_table,
    get_first_available_table,
    stash_db_available,
    _refresh_stash_db
)


class TestPreferConfiguredPath:
    """Test _prefer_configured_path function."""
    
    def test_non_windows_path(self):
        """Test non-Windows path handling."""
        test_path = Path("/usr/local/bin")
        
        with patch('os.name', 'posix'):
            result = _prefer_configured_path(test_path)
            # Should call resolve() on non-Windows
            assert isinstance(result, Path)
    
    @patch('os.name', 'nt')
    def test_windows_drive_path(self):
        """Test Windows drive path handling."""
        # Create a mock path that looks like a Windows drive path
        with patch('pathlib.Path') as mock_path_class:
            mock_path = Mock()
            mock_path.drive = 'C:'
            mock_path.absolute.return_value = Path("C:/Users/test")
            mock_path_class.return_value = mock_path
            
            result = _prefer_configured_path(mock_path)
            
            mock_path.absolute.assert_called_once()
            assert result == Path("C:/Users/test")
    
    @patch('os.name', 'nt')
    def test_windows_non_drive_path(self):
        """Test Windows non-drive path handling."""
        # Create a mock path without drive
        with patch('pathlib.Path') as mock_path_class:
            mock_path = Mock()
            mock_path.drive = ''
            mock_path.resolve.return_value = Path("/full/path")
            mock_path_class.return_value = mock_path
            
            result = _prefer_configured_path(mock_path)
            
            mock_path.resolve.assert_called_once_with(strict=False)
            assert result == Path("/full/path")
    
    def test_exception_handling(self):
        """Test exception handling in path resolution."""
        # Use a mock path to control behavior - make sure it's not Windows drive path
        mock_path = Mock(spec=Path)
        mock_path.drive = ''  # Not a Windows drive path
        mock_path.resolve.side_effect = Exception("Test error")
        mock_path.absolute.return_value = Path("/absolute/path")
        
        with patch('os.name', 'posix'):  # Ensure we're not in Windows mode
            result = _prefer_configured_path(mock_path)
        
        mock_path.absolute.assert_called_once()
        assert result == Path("/absolute/path")
    
    def test_double_exception_handling(self):
        """Test handling when both resolve and absolute fail."""
        mock_path = Mock(spec=Path)
        mock_path.resolve.side_effect = Exception("Resolve error")
        mock_path.absolute.side_effect = Exception("Absolute error")
        
        result = _prefer_configured_path(mock_path)
        
        # Should return original path when all methods fail
        assert result == mock_path


class TestResolveDbPath:
    """Test _resolve_db_path function."""
    
    def setup_method(self):
        """Clear cached path before each test."""
        import stash_ai_server.utils.stash_db as stash_db_module
        stash_db_module._CACHED_DB_PATH = None
    
    @patch('stash_ai_server.utils.stash_db.sys_get')
    @patch('stash_ai_server.utils.stash_db.mutate_path_for_backend')
    def test_resolve_db_path_success(self, mock_mutate, mock_sys_get):
        """Test successful DB path resolution."""
        mock_sys_get.return_value = "/path/to/stash.db"
        mock_mutate.return_value = "/mutated/path/to/stash.db"
        
        with patch('pathlib.Path.expanduser') as mock_expanduser, \
             patch('pathlib.Path.exists', return_value=True):
            mock_expanduser.return_value = Path("/mutated/path/to/stash.db")
            
            result = _resolve_db_path()
            
            assert result == Path("/mutated/path/to/stash.db")
            mock_sys_get.assert_called_once_with("STASH_DB_PATH")
            mock_mutate.assert_called_once_with("/path/to/stash.db")
    
    @patch('stash_ai_server.utils.stash_db.sys_get')
    def test_resolve_db_path_no_setting(self, mock_sys_get):
        """Test DB path resolution with no setting."""
        mock_sys_get.return_value = None
        
        result = _resolve_db_path()
        
        assert result is None
    
    @patch('stash_ai_server.utils.stash_db.sys_get')
    def test_resolve_db_path_invalid_setting(self, mock_sys_get):
        """Test DB path resolution with invalid setting."""
        mock_sys_get.return_value = "REPLACE_WITH_DB_PATH"
        
        result = _resolve_db_path()
        
        assert result is None
    
    @patch('stash_ai_server.utils.stash_db.sys_get')
    @patch('stash_ai_server.utils.stash_db.mutate_path_for_backend')
    def test_resolve_db_path_not_exists(self, mock_mutate, mock_sys_get):
        """Test DB path resolution when file doesn't exist."""
        mock_sys_get.return_value = "/path/to/nonexistent.db"
        mock_mutate.return_value = "/mutated/path/to/nonexistent.db"
        
        with patch('pathlib.Path.expanduser') as mock_expanduser, \
             patch('pathlib.Path.exists', return_value=False):
            mock_expanduser.return_value = Path("/mutated/path/to/nonexistent.db")
            
            result = _resolve_db_path()
            
            assert result is None
    
    def test_resolve_db_path_cached(self):
        """Test that cached path is returned."""
        import stash_ai_server.utils.stash_db as stash_db_module
        cached_path = Path("/cached/path.db")
        stash_db_module._CACHED_DB_PATH = cached_path
        
        result = _resolve_db_path()
        
        assert result == cached_path


class TestGetStashDbPath:
    """Test get_stash_db_path function."""
    
    @patch('stash_ai_server.utils.stash_db._resolve_db_path')
    def test_get_stash_db_path_no_refresh(self, mock_resolve):
        """Test getting DB path without refresh."""
        mock_resolve.return_value = Path("/test/path.db")
        
        result = get_stash_db_path(refresh=False)
        
        assert result == Path("/test/path.db")
        mock_resolve.assert_called_once()
    
    @patch('stash_ai_server.utils.stash_db._resolve_db_path')
    def test_get_stash_db_path_with_refresh(self, mock_resolve):
        """Test getting DB path with refresh."""
        import stash_ai_server.utils.stash_db as stash_db_module
        stash_db_module._CACHED_DB_PATH = Path("/old/path.db")
        
        mock_resolve.return_value = Path("/new/path.db")
        
        result = get_stash_db_path(refresh=True)
        
        assert result == Path("/new/path.db")
        assert stash_db_module._CACHED_DB_PATH is None  # Should be cleared
        mock_resolve.assert_called_once()


class TestBuildEngineForPath:
    """Test _build_engine_for_path function."""
    
    @patch('sqlite3.connect')
    @patch('sqlalchemy.create_engine')
    def test_build_engine_success(self, mock_create_engine, mock_sqlite_connect):
        """Test successful engine creation."""
        test_path = Path("/test/stash.db")
        mock_engine = Mock()
        mock_create_engine.return_value = mock_engine
        mock_conn = Mock()
        mock_engine.connect.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = Mock(return_value=None)
        
        result = _build_engine_for_path(test_path)
        
        assert result == mock_engine
        mock_create_engine.assert_called_once()
        # Verify read-only URI was used
        args, kwargs = mock_create_engine.call_args
        assert "sqlite+pysqlite://" in args
        assert "creator" in kwargs
    
    @patch('sqlite3.connect', side_effect=Exception("Connection failed"))
    @patch('sqlalchemy.create_engine')
    def test_build_engine_failure(self, mock_create_engine, mock_sqlite_connect):
        """Test engine creation failure."""
        test_path = Path("/test/stash.db")
        mock_engine = Mock()
        mock_create_engine.return_value = mock_engine
        mock_engine.connect.side_effect = Exception("Test connection failed")
        
        result = _build_engine_for_path(test_path)
        
        assert result is None


class TestGetStashEngine:
    """Test get_stash_engine function."""
    
    def setup_method(self):
        """Clear engine state before each test."""
        import stash_ai_server.utils.stash_db as stash_db_module
        stash_db_module._STASH_ENGINE = None
        stash_db_module._STASH_SESSION_FACTORY = None
        stash_db_module._STASH_DB_PATH = None
        stash_db_module._CACHED_DB_PATH = None
    
    @patch('stash_ai_server.utils.stash_db._resolve_db_path')
    def test_get_stash_engine_no_path(self, mock_resolve):
        """Test engine creation with no DB path."""
        mock_resolve.return_value = None
        
        result = get_stash_engine()
        
        assert result is None
    
    @patch('stash_ai_server.utils.stash_db._resolve_db_path')
    def test_get_stash_engine_path_not_exists(self, mock_resolve):
        """Test engine creation when DB path doesn't exist."""
        # Use a mock path that doesn't exist
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = False
        mock_resolve.return_value = mock_path
        
        result = get_stash_engine()
        
        assert result is None
    
    @patch('stash_ai_server.utils.stash_db._resolve_db_path')
    @patch('stash_ai_server.utils.stash_db._build_engine_for_path')
    def test_get_stash_engine_success(self, mock_build_engine, mock_resolve):
        """Test successful engine creation."""
        # Use a mock path that exists
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve.return_value = mock_path
        mock_engine = Mock()
        mock_build_engine.return_value = mock_engine
        
        result = get_stash_engine()
        
        assert result == mock_engine
        mock_build_engine.assert_called_once_with(mock_path)
    
    @patch('stash_ai_server.utils.stash_db._resolve_db_path')
    @patch('stash_ai_server.utils.stash_db._build_engine_for_path')
    def test_get_stash_engine_cached(self, mock_build_engine, mock_resolve):
        """Test that cached engine is returned."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        # Use a mock path that exists
        mock_path = Mock(spec=Path)
        mock_path.exists.return_value = True
        mock_resolve.return_value = mock_path
        cached_engine = Mock()
        stash_db_module._STASH_ENGINE = cached_engine
        stash_db_module._STASH_DB_PATH = mock_path
        
        result = get_stash_engine()
        
        assert result == cached_engine
        mock_build_engine.assert_not_called()  # Should use cached


class TestGetStashSessionmaker:
    """Test get_stash_sessionmaker function."""
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    def test_get_sessionmaker_no_engine(self, mock_get_engine):
        """Test sessionmaker creation with no engine."""
        mock_get_engine.return_value = None
        
        result = get_stash_sessionmaker()
        
        assert result is None
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    def test_get_sessionmaker_success(self, mock_get_engine):
        """Test successful sessionmaker creation."""
        mock_engine = Mock()
        mock_get_engine.return_value = mock_engine
        
        # Mock the global session factory
        import stash_ai_server.utils.stash_db as stash_db_module
        mock_factory = Mock()
        stash_db_module._STASH_SESSION_FACTORY = mock_factory
        
        result = get_stash_sessionmaker()
        
        assert result == mock_factory


class TestStashDbSession:
    """Test stash_db_session context manager."""
    
    @patch('stash_ai_server.utils.stash_db.get_stash_sessionmaker')
    def test_stash_db_session_no_factory(self, mock_get_sessionmaker):
        """Test session context manager with no factory."""
        mock_get_sessionmaker.return_value = None
        
        with pytest.raises(RuntimeError, match="Stash database is not configured"):
            with stash_db_session():
                pass
    
    @patch('stash_ai_server.utils.stash_db.get_stash_sessionmaker')
    def test_stash_db_session_success(self, mock_get_sessionmaker):
        """Test successful session context manager."""
        mock_session = Mock()
        mock_factory = Mock(return_value=mock_session)
        mock_get_sessionmaker.return_value = mock_factory
        
        with stash_db_session() as session:
            assert session == mock_session
            # Should execute pragma
            mock_session.execute.assert_called_once()
        
        # Should close session
        mock_session.close.assert_called_once()
    
    @patch('stash_ai_server.utils.stash_db.get_stash_sessionmaker')
    def test_stash_db_session_exception(self, mock_get_sessionmaker):
        """Test session context manager with exception."""
        mock_session = Mock()
        mock_factory = Mock(return_value=mock_session)
        mock_get_sessionmaker.return_value = mock_factory
        
        with pytest.raises(ValueError):
            with stash_db_session() as session:
                raise ValueError("Test exception")
        
        # Should still close session
        mock_session.close.assert_called_once()


class TestGetStashTable:
    """Test get_stash_table function."""
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    def test_get_stash_table_no_engine(self, mock_get_engine):
        """Test table reflection with no engine."""
        mock_get_engine.return_value = None
        
        result = get_stash_table("scenes")
        
        assert result is None
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    def test_get_stash_table_cached(self, mock_get_engine):
        """Test cached table retrieval."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        mock_engine = Mock()
        mock_get_engine.return_value = mock_engine
        cached_table = Mock()
        stash_db_module._TABLE_CACHE["scenes"] = cached_table
        
        result = get_stash_table("scenes")
        
        assert result == cached_table
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    @patch('sqlalchemy.Table')
    def test_get_stash_table_success(self, mock_table_class, mock_get_engine):
        """Test successful table reflection."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        mock_engine = Mock()
        mock_get_engine.return_value = mock_engine
        mock_table = Mock()
        mock_table_class.return_value = mock_table
        stash_db_module._TABLE_CACHE = {}  # Clear cache
        
        result = get_stash_table("scenes")
        
        assert result == mock_table
        assert stash_db_module._TABLE_CACHE["scenes"] == mock_table
        mock_table_class.assert_called_once()
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    @patch('sqlalchemy.Table', side_effect=Exception("Reflection failed"))
    def test_get_stash_table_failure_required(self, mock_table_class, mock_get_engine):
        """Test table reflection failure with required=True."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        mock_engine = Mock()
        mock_get_engine.return_value = mock_engine
        stash_db_module._TABLE_CACHE = {}
        
        result = get_stash_table("nonexistent", required=True)
        
        assert result is None
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    @patch('sqlalchemy.Table', side_effect=Exception("Reflection failed"))
    def test_get_stash_table_failure_not_required(self, mock_table_class, mock_get_engine):
        """Test table reflection failure with required=False."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        mock_engine = Mock()
        mock_get_engine.return_value = mock_engine
        stash_db_module._TABLE_CACHE = {}
        
        result = get_stash_table("nonexistent", required=False)
        
        assert result is None


class TestGetFirstAvailableTable:
    """Test get_first_available_table function."""
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    def test_get_first_available_table_no_engine(self, mock_get_engine):
        """Test with no engine available."""
        mock_get_engine.return_value = None
        
        result = get_first_available_table("scenes", "images")
        
        assert result is None
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    def test_get_first_available_table_cached(self, mock_get_engine):
        """Test with cached table."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        mock_engine = Mock()
        mock_get_engine.return_value = mock_engine
        cached_table = Mock()
        cached_table.c.get.return_value = Mock()  # Has required columns
        stash_db_module._TABLE_CACHE["scenes"] = cached_table
        
        result = get_first_available_table("scenes", "images", required_columns=("id", "title"))
        
        assert result == cached_table
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    @patch('sqlalchemy.Table')
    def test_get_first_available_table_reflection(self, mock_table_class, mock_get_engine):
        """Test with table reflection."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        mock_engine = Mock()
        mock_get_engine.return_value = mock_engine
        mock_table = Mock()
        mock_table.c.get.return_value = Mock()  # Has required columns
        mock_table_class.return_value = mock_table
        stash_db_module._TABLE_CACHE = {}
        
        result = get_first_available_table("scenes", required_columns=("id",))
        
        assert result == mock_table
        assert stash_db_module._TABLE_CACHE["scenes"] == mock_table
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    def test_get_first_available_table_missing_columns(self, mock_get_engine):
        """Test with table missing required columns."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        mock_engine = Mock()
        mock_get_engine.return_value = mock_engine
        cached_table = Mock()
        cached_table.c.get.return_value = None  # Missing required column
        stash_db_module._TABLE_CACHE["scenes"] = cached_table
        
        result = get_first_available_table("scenes", required_columns=("missing_column",))
        
        assert result is None


class TestStashDbAvailable:
    """Test stash_db_available function."""
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    def test_stash_db_available_true(self, mock_get_engine):
        """Test when database is available."""
        mock_get_engine.return_value = Mock()
        
        result = stash_db_available()
        
        assert result is True
    
    @patch('stash_ai_server.utils.stash_db.get_stash_engine')
    def test_stash_db_available_false(self, mock_get_engine):
        """Test when database is not available."""
        mock_get_engine.return_value = None
        
        result = stash_db_available()
        
        assert result is False


class TestRefreshStashDb:
    """Test _refresh_stash_db function."""
    
    def test_refresh_stash_db(self):
        """Test database refresh function."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        # Set some state
        stash_db_module._STASH_ENGINE = Mock()
        stash_db_module._STASH_SESSION_FACTORY = Mock()
        stash_db_module._STASH_DB_PATH = Path("/test/path.db")
        
        _refresh_stash_db()
        
        # Should clear all state
        assert stash_db_module._STASH_ENGINE is None
        assert stash_db_module._STASH_SESSION_FACTORY is None
        assert stash_db_module._STASH_DB_PATH is None


class TestDisposeLockedIntegration:
    """Test _dispose_locked function integration."""
    
    def test_dispose_locked_with_engine(self):
        """Test disposal with active engine."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        mock_engine = Mock()
        stash_db_module._STASH_ENGINE = mock_engine
        stash_db_module._STASH_SESSION_FACTORY = Mock()
        stash_db_module._STASH_DB_PATH = Path("/test/path.db")
        stash_db_module._METADATA = Mock()
        stash_db_module._TABLE_CACHE = {"test": Mock()}
        
        _dispose_locked()
        
        mock_engine.dispose.assert_called_once()
        assert stash_db_module._STASH_ENGINE is None
        assert stash_db_module._STASH_SESSION_FACTORY is None
        assert stash_db_module._STASH_DB_PATH is None
        assert stash_db_module._METADATA is None
        assert stash_db_module._TABLE_CACHE == {}
    
    def test_dispose_locked_engine_exception(self):
        """Test disposal when engine disposal raises exception."""
        import stash_ai_server.utils.stash_db as stash_db_module
        
        mock_engine = Mock()
        mock_engine.dispose.side_effect = Exception("Disposal failed")
        stash_db_module._STASH_ENGINE = mock_engine
        
        # Should not raise exception
        _dispose_locked()
        
        assert stash_db_module._STASH_ENGINE is None