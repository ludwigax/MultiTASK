from . import AsyncCake, ThreadedCake
from .utils import oai as openai
from .utils import crossref as crf
from .utils import pdf as pdf

import os
import warnings
from typing import List, Tuple, Dict, Union, Optional, Any
import itertools

def batch_query_openai(
        messages: List[List],
        save_paths: Optional[List[str]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        proxy: Optional[str] = None,
        rpm_cap = 1000,
        max_workers = 5,
        random_delay = False, # avoid for 429 error
        chat_params: Dict[str, Any] = {},
    ) -> List:
    """
    chat_params: {
    model: str (required),
    reasoning_effort: str,
    n: int,
    seed: int,
    temperature: int,
    max_completion_tokens: int
    }
    """

    if save_paths and (len(messages) != len(save_paths)):
        raise ValueError("The number of save paths must match the number of messages.")
    
    if not (os.environ.get("OPENAI_API_KEY") or api_key):
        raise ValueError("The 'OPENAI_API_KEY' is not set.")
    
    if not chat_params.get("model"):
        chat_params["model"] = "gpt-4o-mini"

    if (not chat_params["model"] in ["o1-mini", "o3-mini"]) \
        and chat_params.get("reasoning_effort") is not None:
        warnings.warn("The 'reasoning_effort' field is only available for the 'o1-mini' and 'o3-mini' models.")
        chat_params["reasoning_effort"] = None
    
    if base_url:
        print("Base URL is set:", base_url)

    if proxy:
        print("Proxy is set:", proxy)
    
    if random_delay:
        print("Random delay is enabled.")

    if not save_paths:
        print("No save paths provided. Results will not be immediacy saved.")

    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url
    if proxy:
        os.environ["PROXY"] = proxy

    tasks = []
    for i in range(len(messages)):
        task = {
            "name": f"Task {i}",
            "data": {
                "messages": messages[i], **chat_params
            }
        }
        if save_paths:
            task["save_path"] = save_paths[i]
        tasks.append(task)

    async_cake = AsyncCake(
        worker=openai.openai_post,
        helper=openai.openai_parse,
        max_workers=max_workers,
        max_concurrent_requests=max_workers,
        rpm_cap=rpm_cap,
        random_delay=random_delay,
        max_retries=3,
    )

    results = async_cake.run(tasks)
    return results


def oai_price_calculator(token_usages: Union[Dict, List[Dict]], model_name: str) -> float:
    """
    Args:
        token_usage (Dict or List[Dict]): 
        model_name (str): e.g. "gpt-4", "gpt-4-32k", "gpt-3.5-turbo"。

    Returns:
        float: total cost in USD.
    """

    pricing = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "o1": {"input": 15.00, "output": 60.00},
        "o1-mini": {"input": 1.10, "output": 4.40},
        "o3-mini": {"input": 1.10, "output": 4.40},
        "qwen-max": {"input": 0.34, "output": 1.37},
        "qwen-max-latest": {"input": 0.34, "output": 1.37},
    }

    if model_name not in pricing:
        raise ValueError(f"The model '{model_name}' is not supported.")

    if isinstance(token_usages, dict):
        token_usages = [token_usages]

    total_cost = 0.0
    for usage in token_usages:
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        input_price_per_1m = pricing[model_name]["input"]
        output_price_per_1m = pricing[model_name]["output"]

        input_cost = (prompt_tokens) / 1e6 * input_price_per_1m
        output_cost = (completion_tokens) / 1e6 * output_price_per_1m

        total_cost += input_cost + output_cost
    return round(total_cost, 6)


def batch_query_crossref(
        wanteds: List[Dict],
        mailto: Optional[str] = None,
        proxy: Optional[str] = None,
        rpm_cap = 1000,
        max_workers = 5,
        random_delay = False, # avoid for 429 error
        query_params: Dict[str, Any] = {},
    ) -> List:
    """
    Queries Crossref API with multiple tasks using DOI or keywords in parallel.

    Args:
        wanteds (list): List of dicts with 'dois', 'query', 'n', and 'batch_size'.
        mailto (str): Email for faster API response.
        proxy (str): Proxy server address.
        rpm_cap (int): Max requests per minute.
        random_delay (bool): Enable random delay to avoid 429 errors.
        query_params (dict): Query parameters dictionary, suitable for global settings, such as:
            filter: Filter condition (e.g. "type:journal-article")
            sort: Sort field (e.g. "score", "updated", "deposited", "published", "issued")
            order: Sort order (e.g. "asc", "desc")
            select: Select returned fields (e.g. "DOI,title,author")
            etc.

    Returns:
        list: Tuples of task index and result.
    """
    if proxy:
        print("Proxy is set:", proxy)
        os.environ["PROXY"] = proxy
    
    if random_delay:
        print("Random delay is enabled.")

    def process_wanted(wanted: Dict) -> List[Dict]:
        """
        `process_wanted` processes the wanted dictionary to extract the query and DOI list.
        """
        if "dois" in wanted:
            _type = "doi"
        elif "query" in wanted:
            _type = "query"
        else:
            raise ValueError("Either 'query' or 'doi_list' must be provided.")
        
        mytasks = []
        if _type == "doi":
            doi_list = wanted.get("dois", [])
            
            for i, doi in enumerate(doi_list):
                mytasks.append({
                    "name": f"DOI-{i}",
                    "params": {
                        "doi": doi,
                        "mailto": mailto,
                    },
                    "task_type": _type
                })
        elif _type == "query":
            query = wanted.get("query", "")
            n = wanted.get("n", 50)
            batch_size = wanted.get("batch_size", 20)

            num_batches = (n + batch_size - 1) // batch_size
            for i in range(num_batches):
                current_batch_size = min(batch_size, n - i * batch_size)
                mytasks.append({
                    "name": f"Query-{i}",
                    "params": {
                        "query": query,
                        "rows": current_batch_size,
                        "offset": i * batch_size,
                        "mailto": mailto,
                        **query_params
                    },
                    "task_type": _type
                })
        
        return mytasks

    tasks = []
    indices = []
    for i, wt in enumerate(wanteds):
        prelength = len(tasks)
        tasks.extend(process_wanted(wt))
        indices.extend([i] * (len(tasks) - prelength))

    async def dynamic_worker(session, task_type, **kwargs):
        """
        dynamic_worker for Crossref API, dynamically selects the worker function based on task type.
        """
        if task_type == "doi":
            return await crf.crossref_doi_scrap(session, **kwargs)
        else:
            return await crf.crossref_kwd_scrap(session, **kwargs)

    async_cake = AsyncCake(
        worker=dynamic_worker,
        helper=crf.crossref_parse,
        max_workers=max_workers,
        max_concurrent_requests=max_workers,
        rpm_cap=rpm_cap,
        random_delay=random_delay,
        max_retries=3,
    )

    results = async_cake.run(tasks)
    
    wanted_results = []
    for i in range(len(wanteds)):
        tmp_res = []
        for j, res in enumerate(results):
            if indices[j] == i:
                tmp_res.append(res)

        if "query" in wanteds[i]:
            tmp_res = list(itertools.chain.from_iterable(tmp_res))
        wanted_results.append((i, tmp_res))
    return wanted_results



# Example usage of the functions in this module
if __name__ == "__main__":
    EXAMPLE = False

    if EXAMPLE:
        # OpenAI API 调用示例
        messages = [
            [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is the capital of France?"}
            ],
            [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is the capital of Germany?"}
            ]
        ]
        results = batch_query_openai(
            messages=messages,
            chat_params={"model": "gpt-4o-mini"},
            random_delay=True
        )
        print(results)
        
        # Crossref API调用示例
        wanteds = [
            # DOI列表查询
            {
                "dois": [
                    "10.1038/s41586-020-2649-2",
                    "10.1126/science.aaa8415"
                ]
            },
            # 关键词查询
            {
                "query": "quantum computing",
                "n": 10,
                "batch_size": 5
            },
            # 另一个关键词查询
            {
                "query": "artificial intelligence",
                "n": 10,
                "batch_size": 5
            }
        ]
        
        results = batch_query_crossref(
            wanteds=wanteds,
            mailto="your.email@example.com",
            query_params={
                "filter": "type:journal-article",
                "sort": "relevance",
                "order": "desc"
            }
        )
        print(results)