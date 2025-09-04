# Multask Package

Multask is an efficient Python library for handling concurrent API requests and data processing tasks. It provides a powerful framework for asynchronous and threaded task execution, with built-in error handling, rate limiting, and circuit breaker patterns.

## Key Features

- **Modern Architecture**: Built on `AsyncExecutor` and `ThreadExecutor` with advanced error handling
- **Intelligent Error Handling**: Automatic classification and handling of different error types
- **Circuit Breaker Pattern**: Fault tolerance with user interaction options
- **Adaptive Rate Limiting**: Smart rate limiting with backoff and recovery
- **Optional Dependencies**: Graceful handling of missing dependencies
- **Predefined API Interfaces**:
  - OpenAI API (supports GPT series models and cost tracking)
  - CrossRef API (academic literature metadata retrieval)  
  - PDF Processing (document text extraction)

## Installation

```bash
pip install multask
```

## Quick Start

### Using Predefined API Interfaces

```python
from multask import apis

# Check available APIs
print("Available APIs:", apis.get_available_apis())

# OpenAI API
if 'openai' in apis.get_available_apis():
    messages_list = [
        [{"role": "user", "content": "What is machine learning?"}],
        [{"role": "user", "content": "Explain quantum computing."}]
    ]
    
    results = await apis.openai_batch_query(
        messages_list=messages_list,
        model="gpt-4o-mini",
        max_workers=3,
        rate_limit_rpm=100
    )
    
    # Calculate cost
    token_usages = [r.get('token_usage', {}) for r in results]
    cost = apis.openai_price_calculator(token_usages, "gpt-4o-mini")
    print(f"Total cost: ${cost}")

# CrossRef API
if 'crossref' in apis.get_available_apis():
    requests = [
        {'type': 'doi', 'doi': '10.1038/nature12373'},
        {'type': 'query', 'query': 'machine learning', 'rows': 5}
    ]
    
    results = await apis.crossref_batch_query(
        requests=requests,
        mailto="researcher@university.edu",
        max_workers=2
    )

# PDF Processing
if 'pdf' in apis.get_available_apis():
    results = await apis.pdf_batch_extract(
        pdf_paths=["doc1.pdf", "doc2.pdf"],
        output_dir="extracted_texts",
        method="advanced"
    )
```

### Using Core Executors for Custom Tasks

```python
import multask
import aiohttp

async def custom_api_worker(session, endpoint, api_key, **kwargs):
    headers = {"Authorization": f"Bearer {api_key}"}
    async with session.get(endpoint, headers=headers) as response:
        return await response.json()

# Create executor with shared context
async with aiohttp.ClientSession() as session:
    executor = multask.AsyncExecutor(
        worker=custom_api_worker,
        shared_context={
            "session": session,
            "api_key": "your-api-key"
        },
        max_workers=5,
        rate_limit_config=multask.RateLimitConfig(base_rpm=100),
        circuit_breaker_config=multask.CircuitBreakerConfig(failure_threshold=3)
    )
    
    tasks = [
        {"endpoint": "https://api.example.com/users"},
        {"endpoint": "https://api.example.com/posts"}
    ]
    
    results = await executor.execute(tasks)
```

## Architecture

### Core Components

- **`AsyncExecutor`**: Modern asynchronous task executor with intelligent error handling
- **`ThreadExecutor`**: Thread-based executor for CPU-bound or synchronous tasks
- **`RateLimiter`**: Adaptive rate limiting with backoff and recovery
- **`CircuitBreaker`**: Fault tolerance pattern with user interaction options

### Exception Hierarchy

- **`MultaskError`**: Base exception with severity classification
- **`InternetError`**: Connectivity issues (triggers circuit breaker)
- **`FatalError`**: Programming errors (stops execution)
- **`RateLimitError`**: Rate limiting with adaptive backoff
- **`NetworkInstabilityError`**: Temporary issues (retryable)

### Predefined API Interfaces

- **`apis.openai_*`**: OpenAI API integration with cost tracking
- **`apis.crossref_*`**: CrossRef academic literature API
- **`apis.pdf_*`**: PDF document processing (optional dependency)

## Installation Options

### Basic Installation
```bash
pip install multask
```

### With Optional Dependencies
```bash
# For PDF processing
pip install multask[pdf]  # or: pip install pdfplumber pdfminer.six

# For development
pip install multask[dev]  # includes testing dependencies
```

## Advanced Features

### Error Handling and Circuit Breaker

```python
from multask import AsyncExecutor, CircuitBreakerConfig, RateLimitConfig

executor = AsyncExecutor(
    worker=my_worker,
    circuit_breaker_config=CircuitBreakerConfig(
        failure_threshold=5,  # Open circuit after 5 failures
        timeout=60.0,        # Try half-open after 60 seconds
        success_threshold=3   # Close after 3 successes
    ),
    rate_limit_config=RateLimitConfig(
        base_rpm=100,                    # Base rate limit
        rate_limit_backoff_factor=2.0,   # Backoff multiplier
        max_backoff_factor=10.0          # Maximum backoff
    ),
    enable_user_interaction=True  # Prompt user on circuit breaker
)
```

### Graceful Dependency Handling

```python
from multask import apis

# Check what's available
available_apis = apis.get_available_apis()
print(f"Available: {available_apis}")

# Check specific API
available, error = apis.check_api_availability('pdf')
if not available:
    print(f"PDF processing unavailable: {error}")
```