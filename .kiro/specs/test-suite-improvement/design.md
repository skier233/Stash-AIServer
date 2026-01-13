# Test Suite Improvement Design

## Overview

This design outlines a comprehensive improvement to the Stash AI Server test suite, focusing on removing low-value tests, reducing excessive mocking, and adding critical missing test coverage. The approach prioritizes real application testing over mocked implementations while maintaining fast execution and reliable results.

## Architecture

### Current Test Suite Analysis

**Identified Issues:**
1. **Overly Mocked Tests**: Some tests mock core application logic instead of testing it
2. **Low-Value Tests**: Tests that only validate test infrastructure or basic object creation
3. **Missing Coverage**: Critical components like recommendations, utilities, and services lack tests
4. **Duplicated Validation**: Multiple tests covering the same scenarios without added value
5. **Inconsistent Patterns**: Mixed approaches to testing across different modules

**Strengths to Preserve:**
1. **Real Database Integration**: Tests use actual PostgreSQL with proper isolation
2. **FastAPI Integration**: API tests use the real application, not mocks
3. **Comprehensive Task Manager Tests**: Good coverage of async task management
4. **Plugin System Tests**: Real plugin loading and execution testing

### Improved Test Architecture

```
backend/tests/
├── unit/                          # Pure unit tests (minimal external dependencies)
│   ├── test_utils/               # Utility function tests
│   ├── test_models/              # Database model tests
│   └── test_services/            # Business logic tests
├── integration/                   # Integration tests (multiple components)
│   ├── test_api_integration/     # Full API workflow tests
│   ├── test_plugin_integration/  # Plugin system integration
│   └── test_recommendation_integration/
├── property/                      # Property-based tests
│   ├── test_recommendation_properties/
│   ├── test_utility_properties/
│   └── test_validation_properties/
└── system/                       # System-level tests
    ├── test_startup_shutdown/
    ├── test_health_monitoring/
    └── test_performance/
```

## Components and Interfaces

### Test Removal Strategy

**Tests to Remove:**
1. `test_async_utils.py` - Tests test infrastructure, not application logic
2. Redundant validation tests in `test_api_simple.py` that duplicate `test_api_endpoints.py`
3. Mock-heavy tests that don't exercise real application behavior
4. Infrastructure tests that validate test setup rather than application functionality

**Tests to Refactor:**
1. Consolidate API endpoint tests into comprehensive integration tests
2. Convert overly mocked tests to use real implementations with test data
3. Merge duplicate test scenarios into parameterized tests

### New Test Components

#### 1. Utility Function Tests
```python
# tests/unit/test_utils/test_string_utils.py
# tests/unit/test_utils/test_url_helpers.py  
# tests/unit/test_utils/test_path_mutation.py
# tests/unit/test_utils/test_stash_api.py
```

#### 2. Recommendation System Tests
```python
# tests/unit/test_recommendations/test_registry.py
# tests/unit/test_recommendations/test_storage.py
# tests/integration/test_recommendation_integration/test_end_to_end.py
# tests/property/test_recommendation_properties/test_ranking_properties.py
```

#### 3. Service Layer Tests
```python
# tests/unit/test_services/test_plugin_service.py
# tests/unit/test_services/test_recommendation_service.py
# tests/unit/test_services/test_health_service.py
```

#### 4. Database Model Tests
```python
# tests/unit/test_models/test_interaction_models.py
# tests/unit/test_models/test_plugin_models.py
# tests/unit/test_models/test_recommendation_models.py
```

### Testing Patterns and Standards

#### Real Application Testing Pattern
```python
@pytest.fixture
def app_client(test_database):
    """Client with real app and test database."""
    with TestClient(app) as client:
        yield client

def test_recommendation_workflow(app_client):
    """Test complete recommendation workflow with real components."""
    # Use real API, real database, real business logic
    # Mock only external services (Stash API)
```

#### Property-Based Testing Pattern
```python
from hypothesis import given, strategies as st

@given(st.text(min_size=1), st.integers(min_value=1, max_value=100))
def test_url_helper_properties(url_path, scene_id):
    """Test URL helper properties across many inputs."""
    result = build_scene_url(url_path, scene_id)
    # Verify properties that should always hold
    assert scene_id in result
    assert url_path in result
```

## Data Models

### Test Data Management

**Test Data Strategy:**
1. **Factories**: Use factory functions to create test data objects
2. **Fixtures**: Provide reusable test data through pytest fixtures
3. **Builders**: Use builder pattern for complex test scenarios
4. **Isolation**: Ensure test data doesn't leak between tests

```python
# tests/factories.py
def create_test_scene(scene_id=None, title=None, **kwargs):
    """Factory for creating test scene data."""
    return {
        "id": scene_id or fake.random_int(),
        "title": title or fake.sentence(),
        **kwargs
    }

def create_test_recommendation_request(**kwargs):
    """Factory for creating recommendation request data."""
    return {
        "context": "scenes",
        "limit": 10,
        "config": {},
        **kwargs
    }
```

### Database Test Models

**Model Testing Approach:**
1. Test model validation and constraints
2. Test relationships and foreign keys
3. Test custom methods and properties
4. Test serialization/deserialization

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: API Response Consistency
*For any* valid API request, the response format should be consistent with the OpenAPI specification and include required fields
**Validates: Requirements 7.4**

### Property 2: Recommendation Ranking Stability  
*For any* recommendation request with identical parameters, the ranking order should be deterministic and reproducible
**Validates: Requirements 8.1**

### Property 3: URL Helper Correctness
*For any* valid URL components, the URL helper functions should produce valid URLs that can be parsed back to equivalent components
**Validates: Requirements 5.3**

### Property 4: Data Validation Completeness
*For any* input data, validation functions should either accept valid data unchanged or reject invalid data with descriptive errors
**Validates: Requirements 5.2**

### Property 5: Plugin Loading Idempotency
*For any* valid plugin, loading the plugin multiple times should produce the same result as loading it once
**Validates: Requirements 3.3**

### Property 6: Task Manager State Consistency
*For any* sequence of task operations, the task manager state should remain consistent and all tasks should be trackable
**Validates: Requirements 8.2**

### Property 7: Database Transaction Isolation
*For any* database operation, concurrent operations should not interfere with each other and should maintain data consistency
**Validates: Requirements 2.5**

### Property 8: Configuration Validation Completeness
*For any* configuration input, the validation should either accept valid configurations or reject invalid ones with clear error messages
**Validates: Requirements 8.6**

## Error Handling

### Test Error Scenarios

**Error Handling Test Strategy:**
1. **Invalid Input Tests**: Test all API endpoints with malformed data
2. **Resource Not Found Tests**: Test behavior when requested resources don't exist
3. **External Service Failure Tests**: Test behavior when external dependencies fail
4. **Database Error Tests**: Test behavior during database connectivity issues
5. **Concurrent Access Tests**: Test behavior under high concurrency

### Error Recovery Testing

**Recovery Scenarios:**
1. Database connection recovery after temporary outage
2. Plugin loading recovery after configuration errors
3. Task manager recovery after service failures
4. API rate limiting and backoff behavior

## Testing Strategy

### Test Execution Strategy

**Test Categories:**
1. **Fast Tests** (< 1s each): Unit tests, property tests with small inputs
2. **Medium Tests** (1-5s each): Integration tests, database tests
3. **Slow Tests** (5-30s each): End-to-end workflows, performance tests

**Execution Approach:**
- Run fast tests first for quick feedback
- Run medium tests for comprehensive validation
- Run slow tests for full system validation
- Parallel execution where possible

### Test Selection and Prioritization

**High Priority Tests:**
1. API endpoint functionality and error handling
2. Core business logic (recommendations, task management)
3. Data persistence and retrieval
4. Plugin system functionality
5. Authentication and authorization

**Medium Priority Tests:**
1. Utility function correctness
2. Configuration validation
3. Performance characteristics
4. Error recovery scenarios

**Lower Priority Tests:**
1. Edge case handling for non-critical features
2. Cosmetic validation
3. Development-only functionality

### Property-Based Testing Configuration

**Test Configuration:**
- Minimum 100 iterations per property test
- Configurable iteration count based on test complexity
- Deterministic seed for reproducible failures
- Shrinking enabled for minimal failing examples

**Property Test Categories:**
1. **Data Transformation Properties**: Input/output relationships
2. **Invariant Properties**: Conditions that should always hold
3. **Round-trip Properties**: Serialize/deserialize consistency
4. **Metamorphic Properties**: Relationships between different inputs

### Continuous Integration Strategy

**CI Test Execution:**
1. **Pull Request Tests**: Fast and medium tests only
2. **Main Branch Tests**: All tests including slow tests
3. **Nightly Tests**: Extended property-based testing with more iterations
4. **Performance Tests**: Baseline performance validation

**Test Reporting:**
- Coverage reports for new code
- Performance regression detection
- Flaky test identification and tracking
- Property-based test failure analysis