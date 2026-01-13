import os
import tempfile
import subprocess
import time
import shutil
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from stash_ai_server.core.config import settings as app_settings


# Global shared PostgreSQL instance for all tests
_shared_postgres_instance = None
_postgres_lock = threading.Lock()


@dataclass
class SharedPostgreSQLInstance:
    """Shared PostgreSQL instance for all tests."""
    port: int
    data_dir: Path
    process: subprocess.Popen
    database_url_template: str
    
    def get_database_url(self, db_name: str) -> str:
        """Get database URL for a specific database name."""
        return self.database_url_template.format(db_name=db_name)
    
    def cleanup(self):
        """Clean up the PostgreSQL instance."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            except Exception as e:
                print(f"Error stopping PostgreSQL process: {e}")
        
        if self.data_dir and self.data_dir.exists():
            try:
                shutil.rmtree(self.data_dir)
            except Exception as e:
                print(f"Error cleaning up PostgreSQL data directory: {e}")


def get_shared_postgres_instance() -> Optional[SharedPostgreSQLInstance]:
    """Get or create a shared PostgreSQL instance for all tests."""
    global _shared_postgres_instance
    
    with _postgres_lock:
        if _shared_postgres_instance is not None:
            # Check if the instance is still running
            if _shared_postgres_instance.process.poll() is None:
                return _shared_postgres_instance
            else:
                # Process died, clean up and create new one
                _shared_postgres_instance.cleanup()
                _shared_postgres_instance = None
        
        # Try to create a new shared instance
        _shared_postgres_instance = _create_shared_postgres_instance()
        return _shared_postgres_instance


def _create_shared_postgres_instance() -> Optional[SharedPostgreSQLInstance]:
    """Create a new shared PostgreSQL instance."""
    try:
        # Find PostgreSQL binary
        postgres_bin = _find_postgres_binary()
        if not postgres_bin:
            print("PostgreSQL binary not found for embedded testing")
            return None
        
        # Create temporary data directory
        data_dir = Path(tempfile.mkdtemp(prefix='postgres_shared_test_'))
        
        # Find available port
        import socket
        sock = socket.socket()
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()
        
        print(f"Starting shared embedded PostgreSQL on port {port}")
        
        # Initialize database cluster
        initdb_bin = postgres_bin.parent / ('initdb.exe' if os.name == 'nt' else 'initdb')
        if not initdb_bin.exists():
            initdb_bin = postgres_bin.parent / 'initdb'
        
        if initdb_bin.exists():
            init_result = subprocess.run([
                str(initdb_bin),
                '-D', str(data_dir),
                '-U', 'postgres',
                '--auth-local=trust',
                '--auth-host=trust'
            ], capture_output=True, text=True, timeout=30)
            
            if init_result.returncode != 0:
                print(f"Failed to initialize PostgreSQL: {init_result.stderr}")
                return None
        else:
            print("initdb binary not found")
            return None
        
        # Start PostgreSQL server with Windows-compatible options
        postgres_args = [
            str(postgres_bin),
            '-D', str(data_dir),
            '-p', str(port),
            '-F'  # Don't fork to background
        ]
        
        # Only add Unix socket directory on non-Windows systems
        if os.name != 'nt':
            postgres_args.extend(['-k', str(data_dir)])
        
        process = subprocess.Popen(
            postgres_args,
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )
        
        # Wait for PostgreSQL to start with more conservative timing
        database_url_template = f"postgresql+psycopg://postgres:postgres@127.0.0.1:{port}/{{db_name}}"
        
        # Give PostgreSQL a moment to start up
        time.sleep(2)
        
        for i in range(15):  # 15 second timeout with longer intervals
            if process.poll() is not None:
                # Process exited
                stdout, stderr = process.communicate()
                print(f"PostgreSQL process exited: {stderr.decode()}")
                return None
            
            # Test connection with shorter timeout
            try:
                from sqlalchemy import create_engine, text
                test_url = f"postgresql+psycopg://postgres:postgres@127.0.0.1:{port}/postgres"
                engine = create_engine(
                    test_url, 
                    pool_pre_ping=True, 
                    connect_args={"connect_timeout": 1}
                )
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                engine.dispose()
                
                print(f"Shared embedded PostgreSQL started successfully on port {port}")
                return SharedPostgreSQLInstance(
                    port=port,
                    data_dir=data_dir,
                    process=process,
                    database_url_template=database_url_template
                )
            except Exception as e:
                print(f"Connection attempt {i+1}/15 failed: {e}")
                time.sleep(1)
                continue
        
        print("Shared embedded PostgreSQL failed to start within timeout")
        process.terminate()
        return None
        process.terminate()
        return None
        
    except Exception as e:
        print(f"Error starting shared embedded PostgreSQL: {e}")
        return None


def _find_postgres_binary() -> Optional[Path]:
    """Find PostgreSQL binary for embedded testing."""
    # Common PostgreSQL installation paths
    postgres_paths = [
        # Windows paths
        Path("C:/Program Files/PostgreSQL/*/bin/postgres.exe"),
        Path("C:/PostgreSQL/*/bin/postgres.exe"),
        # Linux/macOS paths
        Path("/usr/bin/postgres"),
        Path("/usr/local/bin/postgres"),
        Path("/opt/postgresql/*/bin/postgres"),
        # Homebrew on macOS
        Path("/opt/homebrew/bin/postgres"),
        Path("/usr/local/opt/postgresql*/bin/postgres"),
    ]
    
    # Check if postgres is in PATH
    try:
        result = subprocess.run(['postgres', '--version'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return Path(shutil.which('postgres'))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    
    # Check common installation paths
    for path_pattern in postgres_paths:
        if '*' in str(path_pattern):
            # Handle wildcard paths
            parent = path_pattern.parent.parent
            if parent.exists():
                for version_dir in parent.glob(path_pattern.parent.name):
                    postgres_bin = version_dir / path_pattern.name
                    if postgres_bin.exists():
                        return postgres_bin
        else:
            if path_pattern.exists():
                return path_pattern
    
    return None


@dataclass
class TestConfig:
    """Test configuration with environment overrides."""
    
    # Database configuration
    database_url: str
    plugin_directory: Path
    database_name: str = "stash_ai_server_test"
    
    # Task configuration
    task_debug: bool = True
    task_loop_interval: float = 0.01
    
    # Logging configuration
    log_level: str = "DEBUG"
    
    # Test-specific settings
    test_timeout: float = 30.0
    websocket_timeout: float = 5.0
    task_completion_timeout: float = 10.0
    
    # Database availability settings
    check_database_availability: bool = True
    use_embedded_postgres: bool = True
    postgres_startup_timeout: float = 30.0
    
    @classmethod
    def create_test_config(cls, test_db_suffix: Optional[str] = None) -> "TestConfig":
        """Create test configuration with isolated database and plugin directory."""
        
        # Create unique test database name with timestamp and random component
        import random
        import time
        
        if test_db_suffix:
            db_suffix = test_db_suffix
        else:
            # Use process ID, timestamp, and random number for uniqueness
            timestamp = int(time.time() * 1000) % 100000  # Last 5 digits of timestamp
            random_num = random.randint(1000, 9999)
            db_suffix = f"test_{os.getpid()}_{timestamp}_{random_num}"
        
        test_db_name = f"stash_ai_server_{db_suffix}"
        
        # For tests, we'll use a different approach:
        # 1. Try to use existing PostgreSQL if available
        # 2. Use shared embedded PostgreSQL for tests if needed
        # 3. Skip database tests if neither is available
        
        db_host = "127.0.0.1"
        db_port = 5432  # Standard PostgreSQL port
        db_user = "postgres"  # Standard user for test databases
        db_password = "postgres"  # Standard password for test databases
        
        # Check for CI environment variables (GitHub Actions, etc.)
        if os.getenv('CI') or os.getenv('GITHUB_ACTIONS'):
            # In CI, use service container or system PostgreSQL
            db_host = os.getenv('POSTGRES_HOST', '127.0.0.1')
            db_port = int(os.getenv('POSTGRES_PORT', '5432'))
            db_user = os.getenv('POSTGRES_USER', 'postgres')
            db_password = os.getenv('POSTGRES_PASSWORD', 'postgres')
        
        test_database_url = f"postgresql+psycopg://{db_user}:{db_password}@{db_host}:{db_port}/{test_db_name}"
        
        # Create isolated test plugin directory
        test_plugin_dir = Path(__file__).parent / "test_plugins"
        test_plugin_dir.mkdir(exist_ok=True)
        
        return cls(
            database_url=test_database_url,
            plugin_directory=test_plugin_dir,
            database_name=test_db_name,
        )
    
    def apply_environment_overrides(self) -> None:
        """Apply test configuration as environment variables."""
        os.environ['DATABASE_URL'] = self.database_url
        os.environ['AI_SERVER_PLUGINS_DIR'] = str(self.plugin_directory)
        os.environ['TASK_DEBUG'] = '1' if self.task_debug else '0'
        os.environ['TASK_LOOP_INTERVAL'] = str(self.task_loop_interval)
        os.environ['AI_SERVER_LOG_LEVEL'] = self.log_level
    
    def cleanup_environment(self) -> None:
        """Clean up test environment variables."""
        # Clean up environment variables
        test_env_vars = [
            'DATABASE_URL',
            'AI_SERVER_PLUGINS_DIR', 
            'TASK_DEBUG',
            'TASK_LOOP_INTERVAL',
            'AI_SERVER_LOG_LEVEL'
        ]
        
        for var in test_env_vars:
            if var in os.environ:
                del os.environ[var]
    
    def check_database_connection(self) -> bool:
        """Check if database is available for connection."""
        try:
            from sqlalchemy import create_engine, text
            
            # Try to connect to postgres database first (for admin operations)
            admin_url = self.database_url.replace(f"/{self.database_name}", "/postgres")
            admin_engine = create_engine(
                admin_url, 
                pool_pre_ping=True, 
                connect_args={"connect_timeout": 2}
            )
            
            with admin_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                
            admin_engine.dispose()
            return True
            
        except Exception as e:
            print(f"Database connection check failed: {e}")
            return False
    
    def ensure_database_available(self) -> bool:
        """Ensure database is available, starting shared embedded instance if necessary."""
        if not self.check_database_availability:
            return True
            
        # First check if database is already available
        if self.check_database_connection():
            return True
        
        print("Database is not available, trying shared embedded PostgreSQL...")
        
        # Try to get or create shared embedded PostgreSQL
        shared_instance = get_shared_postgres_instance()
        if shared_instance:
            # Update our database URL to use the shared instance
            self.database_url = shared_instance.get_database_url(self.database_name)
            
            # Test connection immediately - no additional waiting
            if self.check_database_connection():
                print(f"Using shared embedded PostgreSQL on port {shared_instance.port}")
                return True
            
            print("Shared embedded PostgreSQL started but connection check failed")
            return False
        
        # Provide helpful error message
        print("\n" + "="*60)
        print("DATABASE NOT AVAILABLE FOR TESTING")
        print("="*60)
        print("The test suite requires PostgreSQL to be running.")
        print("\nOptions to fix this:")
        print("1. Install PostgreSQL locally:")
        print("   - Windows: Download from postgresql.org")
        print("   - macOS: brew install postgresql")
        print("   - Linux: apt-get install postgresql")
        print("\n2. Use system PostgreSQL service:")
        print("   - Windows: Start PostgreSQL service")
        print("   - macOS/Linux: sudo systemctl start postgresql")
        print("\n3. Skip database tests:")
        print("   python -m pytest -m 'not database'")
        print("\n4. For CI: Use PostgreSQL service container")
        print("="*60)
        
        return False


# Global test configuration instance
test_config = TestConfig.create_test_config()