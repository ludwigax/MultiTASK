# Multask Package

Multask is a high-performance Python library for concurrent task execution with intelligent error handling, adaptive rate limiting, and circuit breaker patterns. Built with a clean, unified architecture, it provides both core execution engines and ready-to-use API interfaces.

## ✨ Key Features

- **🚀 Unified Architecture**: Clean separation between core execution engines and API interfaces
- **🧠 Intelligent Error Handling**: Automatic error classification with severity-based handling strategies
- **⚡ Dual Execution Models**: AsyncExecutor for I/O-bound tasks, ThreadExecutor for CPU-bound tasks
- **🛡️ Circuit Breaker Pattern**: Fault tolerance with user interaction and adaptive recovery
- **📊 Smart Rate Limiting**: Adaptive rate control with exponential backoff and recovery
- **🔧 Graceful Dependencies**: Optional API modules with automatic fallback
- **📝 Console Error Logging**: Real-time error reporting without interfering with progress bars
- **🎯 Ready-to-Use APIs**: Pre-built **synchronized** interfaces for OpenAI, CrossRef, and PDF processing
- **⚡ Synchronized API Functions**: No `await` needed - APIs internally handle async execution

## 🚀 Quick Start

### Installation

```bash
pip install multask
```

### Basic Usage

#### Using Synchronized API Functions (Recommended)

```python
from multask.apis import openai_batch_query, crossref_batch_query, pdf_batch_extract

# OpenAI API - Synchronous interface
messages = [
    [{"role": "user", "content": "What is machine learning?"}],
    [{"role": "user", "content": "Explain quantum computing."}]
]

results = openai_batch_query(
    messages_list=messages,
    model="gpt-4o-mini",
    max_workers=3,
    enable_error_printing=True
)

print(f"Processed {len(results)} OpenAI requests")

# CrossRef API - Synchronous interface
requests = [
    {'type': 'doi', 'doi': '10.1038/nature12373'},
    {'type': 'query', 'query': 'machine learning', 'rows': 5}
]

results = crossref_batch_query(
    requests=requests,
    mailto="researcher@university.edu",
    max_workers=2
)

print(f"Processed {len(results)} CrossRef requests")
```

#### Using Core Executors (Advanced)

```python
import asyncio
from multask import AsyncExecutor

# Custom async task execution
async def my_worker(data, **kwargs):
    # Your task logic here
    return f"Processed: {data}"

async def main():
    # Create executor with smart controller
    executor = AsyncExecutor(
        worker=my_worker,
        controller_type="smart",
        max_workers=3,
        enable_error_printing=True
    )
    
    # Execute tasks
    tasks = [{"data": f"task_{i}"} for i in range(10)]
    results = await executor.execute(tasks)
    
    for index, result in results:
        print(f"Task {index}: {result}")

asyncio.run(main())
```

## 🏗️ Architecture Overview

### Core Components

#### Executors
- **`AsyncExecutor`**: High-performance async task execution with intelligent error handling
- **`ThreadExecutor`**: Thread-based execution for CPU-bound or synchronous tasks

#### Controllers
- **`BasicController`**: Simple rate limiting with basic retry logic
- **`SmartController`**: Advanced adaptive control with circuit breaker and user interaction

#### Error Handling
- **Unified Exception System**: Automatic error classification and severity-based handling
- **Console Error Logging**: Real-time error reporting with timestamps and task indexing
- **Circuit Breaker Integration**: Fault tolerance with user interaction options

## 📚 API Interfaces

> **Note**: All API batch functions (`openai_batch_query`, `crossref_batch_query`, `pdf_batch_extract`) are **synchronized** - they internally use async execution but provide synchronous interfaces. No `await` needed!

### OpenAI Integration

```python
from multask.apis import OpenAIExecutor, openai_batch_query

# Batch OpenAI requests
messages_list = [
    [{"role": "user", "content": "What is machine learning?"}],
    [{"role": "user", "content": "Explain quantum computing."}]
]

results = openai_batch_query(
    messages_list=messages_list,
    model="gpt-4o-mini",
    max_workers=3,
    enable_error_printing=True
)

# Calculate costs
from multask.apis import openai_price_calculator
cost = openai_price_calculator([r.get('token_usage', {}) for r in results], "gpt-4o-mini")
print(f"Total cost: ${cost}")
```

### CrossRef Academic API

```python
from multask.apis import CrossRefExecutor, crossref_batch_query

# Academic literature queries
requests = [
    {'type': 'doi', 'doi': '10.1038/nature12373'},
    {'type': 'query', 'query': 'machine learning', 'rows': 5}
]

results = crossref_batch_query(
    requests=requests,
    mailto="researcher@university.edu",
    max_workers=2
)
```

### PDF Processing

```python
from multask.apis import PDFExecutor, pdf_batch_extract

# Extract text from PDFs
results = pdf_batch_extract(
    pdf_paths=["doc1.pdf", "doc2.pdf"],
    output_dir="extracted_texts",
    method="advanced"  # or "simple"
)
```

## ⚙️ Advanced Configuration

### Controller Configuration

```python
from multask import AsyncExecutor, SmartController, SmartControllerConfig, RateLimitConfig

# Smart controller with adaptive rate limiting
executor = AsyncExecutor(
    worker=my_worker,
    controller_type="smart",
    smart_controller_config=SmartControllerConfig(
        rate_limit_config=RateLimitConfig(
            max_rpm=100,
            safety_factor=0.9
        ),
        failure_threshold=5,
        circuit_timeout=60.0,
        adaptive_speed_enabled=True
    ),
    enable_user_interaction=True,
    enable_error_printing=True
)
```

### Error Handling and Circuit Breaker

```python
# The system automatically handles:
# - Rate limit errors with exponential backoff
# - Network instability with retry logic
# - Circuit breaker activation with user prompts
# - Fatal errors with immediate termination

# Error messages are printed to console in real-time:
# [2024-01-15 10:30:45] ERROR in task 5: Rate limit exceeded, retrying in 2s (attempt 2/3)
# [2024-01-15 10:30:47] ERROR in task 3: Circuit breaker triggered: Too many failures
```

### Shared Context

```python
import aiohttp

async def api_worker(session, endpoint, **kwargs):
    async with session.get(endpoint) as response:
        return await response.json()

# Use shared context for common parameters
async with aiohttp.ClientSession() as session:
    executor = AsyncExecutor(
        worker=api_worker,
        shared_context={
            "session": session,
            "api_key": "your-api-key"
        },
        max_workers=5
    )
    
    tasks = [
        {"endpoint": "https://api.example.com/users"},
        {"endpoint": "https://api.example.com/posts"}
    ]
    
    results = await executor.execute(tasks)
```

## 🔧 Error Handling System

### Automatic Error Classification

The system automatically classifies errors into severity levels:

- **`RECOVERABLE`**: Temporary issues, retry with backoff
- **`RATE_LIMITED`**: Rate limit exceeded, adaptive backoff
- **`CIRCUIT_BREAKER`**: Too many failures, circuit breaker activation
- **`FATAL`**: Programming errors, immediate termination

### Console Error Logging

```python
# Enable real-time error logging
executor = AsyncExecutor(
    worker=my_worker,
    enable_error_printing=True  # Default: True
)

# Error output format:
# [2024-01-15 10:30:45] ERROR in task 5: Rate limit exceeded, retrying in 2s (attempt 2/3)
# [2024-01-15 10:30:47] ERROR in task 3: Circuit breaker triggered: Too many failures
# [2024-01-15 10:30:47] ERROR in task 3: Action: skip - Skipped due to circuit breaker
```

## 📦 Installation Options

### Basic Installation
```bash
pip install multask
```

### With Optional Dependencies
```bash
# For PDF processing
pip install multask[pdf]

# For development
pip install multask[dev]
```

### Check Available APIs
```python
from multask.apis import get_available_apis, check_api_availability

# List available APIs
print("Available APIs:", get_available_apis())

# Check specific API
available, error = check_api_availability('pdf')
if not available:
    print(f"PDF processing unavailable: {error}")
```

## 🎯 Performance Features

### Progress Tracking
- Real-time progress bars with `tqdm` integration
- Rate limiting visualization
- Error logging without progress bar interference

### Adaptive Rate Limiting
- Intelligent backoff based on error patterns
- Automatic recovery from rate limiting
- Configurable safety factors and limits

### Circuit Breaker
- Automatic failure detection
- User interaction on critical failures
- Configurable thresholds and timeouts

## 🔄 Migration from Previous Versions

The new architecture maintains backward compatibility while providing cleaner interfaces:

```python
# Old way (still works)
from multask import AsyncExecutor, RateLimiter

# New way (recommended)
from multask import AsyncExecutor, BasicController, SmartController
```

## 📖 Examples

### Custom API Integration

```python
import aiohttp
from multask import AsyncExecutor

async def custom_api_worker(session, url, headers=None, **kwargs):
    async with session.get(url, headers=headers) as response:
        response.raise_for_status()
        return await response.json()

async with aiohttp.ClientSession() as session:
    executor = AsyncExecutor(
        worker=custom_api_worker,
        shared_context={"session": session},
        max_workers=5,
        enable_error_printing=True
    )
    
    tasks = [
        {"url": "https://api.example.com/data1"},
        {"url": "https://api.example.com/data2"}
    ]
    
    results = await executor.execute(tasks)
    print(f"Processed {len(results)} tasks")
```

### Thread-based Processing

```python
from multask import ThreadExecutor

def cpu_intensive_worker(data, **kwargs):
    # CPU-intensive task
    result = sum(i**2 for i in range(data))
    return result

executor = ThreadExecutor(
    worker=cpu_intensive_worker,
    max_workers=4,
    enable_error_printing=True
)

tasks = [{"data": 1000} for _ in range(10)]
results = executor.execute(tasks)
```
