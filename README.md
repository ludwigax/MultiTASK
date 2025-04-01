# Multask Package

Multask是一个高效的Python并发请求处理库，专为批量API请求和数据处理任务设计。它提供了强大的异步和线程处理框架，内置了常用API集成，特别优化了对OpenAI API和学术数据API的调用。

## 主要特性

- **高效并发**: 基于`AsyncCake`和`ThreadedCake`的并发框架，支持异步和线程并发模式
- **速率控制**: 内置请求速率限制(RPM)，避免触发API限制
- **自动重试**: 智能重试机制，处理临时网络错误和服务中断
- **进度可视化**: 实时进度条显示任务完成情况和请求速率
- **API集成**: 
  - OpenAI API (支持GPT系列模型的调用和计费计算)
  - Crossref API (学术文献检索)
  - PDF处理工具 (文档解析和处理)

## 安装

```bash
pip install multask
```

## 快速开始

### OpenAI API 批量请求

```python
import multask

messages = [
    [{"role": "system", "content": "You are a helpful assistant."},
     {"role": "user", "content": "What is the capital of France?"}]
] * 5  # 创建5个相同的请求

results = multask.batch_query_openai(
    messages=messages,
    api_key="your_api_key",  # 可选，也可设置环境变量
    proxy="http://your_proxy",  # 可选
    max_workers=5,
    chat_params={"model": "gpt-4o-mini"}
)

# 计算API使用成本
total_cost = multask.oai_price_calculator(
    [r["token_usage"] for r in results], 
    "gpt-4o-mini"
)
print(f"总花费: ${total_cost}")
```

### Crossref 学术文献查询

```python
import multask

queries = ["machine learning", "artificial intelligence", "neural networks"]

results = multask.batch_query_crossref(
    queries=queries,
    mailto="your.email@example.com",  # 可选但推荐，提高请求优先级
    max_workers=3
)

# 打印第一篇文献的标题和引用次数
print(f"标题: {results[0]['title']}")
print(f"引用次数: {results[0]['is_referenced_by_count']}")
```

### 自定义异步任务处理

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

## 核心模块

- **AsyncCake**: 基于asyncio和aiohttp的异步请求框架
- **ThreadedCake**: 基于线程池的并发请求框架
- **OpenAI工具**: OpenAI API集成和辅助函数
- **Crossref工具**: 学术文献API集成
- **PDF工具**: PDF文档处理函数

## 高级功能

### 请求速率控制

```python
# 设置请求速率上限为每分钟60次请求
async_cake = AsyncCake(
    worker=my_worker,
    rpm_cap=60,
    max_workers=5
)
```

### 随机延迟

```python
# 启用随机延迟，避免429错误
results = multask.batch_query_openai(
    messages=messages,
    random_delay=True
)
```

### 并发控制

```python
# 限制最大并发请求数和工作线程数
async_cake = AsyncCake(
    worker=my_worker,
    max_concurrent_requests=10,  # 最大并发请求数
    max_workers=5                # 工作线程数
)
```