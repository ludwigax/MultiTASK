import asyncio
import aiohttp
import itertools
import random
import time
from tqdm import tqdm


class RPM:
    def __init__(self, rpm_cap=1000, safe_mode=True):
        """
        :param rpm_cap: Maximum requests per minute (cap on request rate).
        :param safe_mode: If True, allows 90% of the cap to be used; else, allows full cap.
        """
        self.percentage = 0.9 if safe_mode else 1.0
        self.cap = rpm_cap
        self.stamps = []

    def update(self):
        now_time = time.time()
        self.stamps = [t for t in self.stamps if t > now_time - 60]
        if len(self.stamps) >= self.cap * self.percentage:
            return None
        self.stamps.append(now_time)
        return len(self.stamps)
    
    @property
    def rpm(self):
        now_time = time.time()
        return len([t for t in self.stamps if t > now_time - 60])

class AsyncCake:
    def __init__(
            self, 
            worker: callable, 
            helper: callable=None, 
            post: callable=None, 
            rpm_cap=1000, 
            max_concurrent_requests=5, 
            max_workers=5, 
            max_retries=3,
            random_delay=False
        ):
        """
        :param worker: Main Function need IO time.
        :param parse: Optional Support Function for processing result.
        :param post: Optional function to process results after fetching all result.
        :param rpm_cap: Maximum allowed requests per minute (rate-limiting).
        :param max_concurrent_requests: Maximum number of concurrent requests per worker.
        :param max_workers: Number of worker tasks to spawn.
        :param max_retries: Number of retries for each task in case of failure.
        """
        self._worker = worker
        self._helper = helper
        self._post = post
        self.rpm_obj = RPM(rpm_cap=rpm_cap)
        self.max_concurrent_requests = max_concurrent_requests
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.random_delay = random_delay
        self.task_queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        self.progress_bar = None
        self.rpm_bar = None

    def _init_bars(self, total_tasks):
        self.progress_bar = tqdm(total=total_tasks, desc="Progress", ncols=100)
        self.rpm_bar = tqdm(total=self.rpm_obj.cap, desc="RPM", ncols=100)

    def _close_bars(self):
        if self.progress_bar:
            self.progress_bar.close()
        if self.rpm_bar:
            self.rpm_bar.close()

    def _cancel_bars(self):
        if self.progress_bar:
            self.progress_bar.clear()
            self.progress_bar.close()
        if self.rpm_bar:
            self.rpm_bar.clear()
            self.rpm_bar.close()

    def reset_task_queue(self, tasks):
        self.task_queue = asyncio.Queue()
        for i, task in enumerate(tasks):
            self.task_queue.put_nowait((i, task))
        
        self.semaphore = asyncio.Semaphore(self.max_concurrent_requests)

    async def worker(self, session):
        worker_results = []
        while not self.task_queue.empty():
            rpm = self.rpm_obj.update()
            if not rpm:
                await asyncio.sleep(1)
                continue
            self.rpm_bar.n = rpm
            self.rpm_bar.refresh()

            index, task_params = await self.task_queue.get()

            async with self.semaphore:
                retries = 0
                retrying = False
                while True:
                    self.progress_bar.refresh()
                    self.rpm_bar.refresh()

                    try:
                        if self.random_delay:
                            await asyncio.sleep(random.uniform(0.4, 1 * self.max_workers / 5))

                        
                        data = await self._worker(session, **task_params)
                        if self._helper:
                            data = self._helper(data, params=task_params)
                        worker_results.append((index, data))
                        retrying = False
                        break
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        retries += 1
                        retrying = True
                        if retries > self.max_retries:
                            retrying = False
                            worker_results.append(
                                (index, {"error": "Max retries reached for {name}. {e}".format(name=task_params.get("name"), e=str(e))})
                            )
                            break
                        else:
                            tqdm.write("{e} encountered for {desc}. Retrying {r}/{mr}...".format(e=type(e), desc=str(e), r=retries, mr=self.max_retries)) 
                            await asyncio.sleep(2 ** retries)  # exponential backoff
                    except Exception as e:
                        tqdm.write("{e} encountered for {desc}. Skipping...".format(e=type(e), desc=str(e)))
                        worker_results.append((index, {"error": str(e)}))
                        break
                    finally:
                        if not retrying:
                            self.task_queue.task_done()
                            self.progress_bar.update(1)

        return worker_results

    async def async_cake(self, tasks):
        self.reset_task_queue(tasks)
        self._init_bars(len(tasks))

        async with aiohttp.ClientSession() as session:
            workers = [asyncio.create_task(self.worker(session)) 
                       for _ in range(self.max_workers)]

            try:
                await self.task_queue.join()
                for w in workers:
                    w.cancel()
                results = await asyncio.gather(*workers, return_exceptions=True)
            except asyncio.CancelledError:
                self._cancel_bars()
                raise
            finally:
                self._close_bars()
        results = list(itertools.chain.from_iterable(results))
        results = sorted(results, key=lambda x: x[0], reverse=False)

        if self._post:
            results = self._post(results)
        return results

    def run(self, tasks):
        return asyncio.run(self.async_cake(tasks))


# Debugging functions
async def debug_worker(*args, **kwargs):
    """Scraping function: Simulates a scraping process."""
    await asyncio.sleep(random.uniform(0.1, 0.5))
    return "Scraped data"

async def debug_error_worker(*args, **kwargs):
    """Scraping function: Simulates a scraping process."""
    await asyncio.sleep(random.uniform(0.1, 0.5))
    raise aiohttp.ClientError("Error encountered")

async def debug_online_worker(session, url, *args, **kwargs):
    """Scraping function: Simulates a scraping process."""
    async with session.get(url) as response:
        return await response.text()


if __name__ == "__main__":
    tasks = []
    for i in range(10):
        task = {"name": f"Task {i}", "url": f"https://www.example.com/{i}"}
        tasks.append(task)

    crawler = AsyncCake(worker=debug_error_worker, rpm_cap=500, max_concurrent_requests=5, max_workers=10, max_retries=3)
    results = asyncio.run(crawler.async_cake(tasks))

    print(results)
