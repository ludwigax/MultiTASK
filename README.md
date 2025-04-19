# Multask Package

Multask is an efficient Python library for handling concurrent API requests and data processing tasks. It provides a powerful framework for asynchronous and threaded task execution, optimized for bulk API calls to OpenAI and academic data APIs.

## Key Features

- **Efficient Concurrency**: Built on `AsyncCake` and `ThreadedCake`, supporting both asynchronous and threaded concurrency.
- **Rate Limiting**: Built-in request rate control (RPM) to avoid triggering API rate limits.
- **Automatic Retries**: Intelligent retry mechanism to handle temporary network errors or service interruptions.
- **Cost Calculation**: OpenAI API cost calculator based on token usage.
- **API Integrations**:
  - OpenAI API (supports GPT series models and cost tracking)
  - Crossref API (academic literature retrieval)

## Installation

```bash
pip install multask
```

## Quick Start

### Batch OpenAI API Requests

```python
from multask import batch_query_openai, oai_price_calculator

messages = [
    [{"role": "system", "content": "You are a helpful assistant."},
     {"role": "user", "content": "What is the capital of France?"}]
] * 5  # Create 5 identical requests

results = batch_query_openai(
    messages=messages,
    api_key="your_api_key",  # Optional, can also set via environment variable
    proxy="http://your_proxy",  # Optional
    max_workers=5,
    chat_params={"model": "gpt-4o-mini"},
    random_delay=True  # Avoid 429 errors
)

# Calculate API usage cost
total_cost = oai_price_calculator(
    [r["token_usage"] for r in results], 
    "gpt-4o-mini"
)
print(f"Total cost: ${total_cost}")
```

### Crossref Academic Literature Query

```python
from multask import batch_query_crossref

wanteds = [
    {
        "dois": ["10.1038/s41586-020-2649-2"],
    },
    {
        "query": "quantum computing",
        "n": 10,
        "batch_size": 5
    }
]

results = batch_query_crossref(
    wanteds=wanteds,
    mailto="your.email@example.com",  # Recommended to increase request priority
    max_workers=3
)

# Example to print results
for idx, res in results:
    print(f"Query {idx} returned {len(res)} results")
```

### Custom Asynchronous Task Processing

```python
from multask import AsyncCake

async def custom_worker(session, url, **kwargs):
    async with session.get(url) as response:
        return await response.text()

def process_result(data, **kwargs):
    return {"length": len(data), "preview": data[:100]}

tasks = [
    {"name": "Task 1", "url": "https://example.com/api/1"},
    {"name": "Task 2", "url": "https://example.com/api/2"}
]

async_cake = AsyncCake(
    worker=custom_worker,
    helper=process_result,
    max_workers=5,
    rpm_cap=100
)

results = async_cake.run(tasks)
```

## Core Modules

- **AsyncCake**: An asynchronous request framework based on `asyncio` and `aiohttp`.
- **OpenAI Tools**: OpenAI API integration with cost calculation utilities.
- **Crossref Tools**: Crossref API integration for academic literature queries.

## Advanced Features

### Cost Calculation

```python
# Calculate OpenAI API costs based on token usage
total_cost = oai_price_calculator(
    token_usages=[{"prompt_tokens": 100, "completion_tokens": 200}], 
    model_name="gpt-4o-mini"
)
print(f"Total cost: ${total_cost}")
```

### Request Rate Control and Random Delay

Refer to the OpenAI API batch request example in the quick start section for usage of rate limiting and random delay features.