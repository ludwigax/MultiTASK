from aiohttp import ClientSession
from .. import AsyncCake
import itertools

async def crossref_scrap(session: ClientSession, doi=None, params={}, **kwargs):
    url = f"https://api.crossref.org/works/{doi}"
    async with session.get(url, params=params) as response:
        response.raise_for_status()
        data = await response.json()
        return data
    
async def crossref_batch_scrap(session: ClientSession, params={}, cursor=False, **kwargs):
    url = "https://api.crossref.org/works"

    async with session.get(url, params=params) as response:
        response.raise_for_status()
        data = await response.json()
        return data
    
def crossref_parse(data, filtered_fields=None, **kwargs):
    if data.get("message") and data["message"].get("items"):
        items = data["message"]["items"]
    elif data.get("message"):
        items = [data["message"]]

    metadata_list = []
    for item in items:
        try:
            authors = []
            for author in item.get("author", []):
                # Check for 'name' or 'family' + 'given'
                if "name" in author:
                    authors.append(author["name"])  # Use 'name' field directly
                else:
                    family = author.get("family", "")
                    given = author.get("given", "")
                    authors.append(f"{family}, {given}")  # Format: Last Name, First Name
            
            metadata = {
                "title": item.get("title", [""])[0],  # 获取第一个标题
                "authors": authors,  # 作者列表
                "journal": item.get("container-title", [""])[0] if item.get("container-title", [""]) else "",  # 期刊名
                "volume": item.get("volume", ""),  # 卷号
                "issue": item.get("issue", ""),  # 期号
                "pages": item.get("page", ""),  # 页码
                "published_year": item.get("published-print", {}).get("date-parts", [[None]])[0][0] or 
                                    item.get("published-online", {}).get("date-parts", [[None]])[0][0],  # 发表年份
                "doi": item.get("DOI", ""),  # DOI
                "url": item.get("URL", ""),  # 文章的URL
                "abstract": item.get("abstract", ""),  # 摘要
                "keywords": item.get("keyword", []),  # 关键词
                "language": item.get("language", ""),  # 语言
                "is_referenced_by_count": item.get("is-referenced-by-count", 0),  # 引用次数
                "reference_count": len(item.get("reference", [])),  # 引用的文献数量
                "citation_type": item.get("type", ""),  # 文献类型（例如：期刊文章、会议论文等）
                "publisher": item.get("publisher", ""),  # 出版商
                "issn": item.get("ISSN", ""),  # ISSN (如果存在)
                "issn_type": item.get("ISSN-type", ""),  # ISSN 类型（例如 print、online）
                "creator_orcid": item.get("creator-orcid", ""),  # ORCID（作者的ORCID标识符）
                "version": item.get("version", ""),  # 版本（如预印本等）
                "published_date": item.get("published", ""),  # 发表的完整日期（ISO 8601格式）
                "reference_list_length": len(item.get("reference", [])),  # 引用列表长度
            }
            metadata_list.append(metadata)
        except Exception as e:
            print(f"Error parsing metadata: {e}")
            metadata_list.append({"doi": item.get("DOI", ""), "error": str(e)})

    if len(metadata_list) == 1:
        return metadata_list[0]
    return metadata_list


def crossref_post(results):
    results = list(itertools.chain.from_iterable([x[1] for x in results]))
    return results


if __name__=="__main__":
    tasks = []
    for i in range(5):
        task = {
            "name": f"Task {i}",
            "params": {
                "query": "boron nitride",
                "offset": i*10,
                "rows": 10
            }
        }
        tasks.append(task)
    
    async_crawler = AsyncCake(
        worker=crossref_batch_scrap,
        helper=crossref_parse,
        post=crossref_post,
        max_workers=3,
    )
    results = async_crawler.run(tasks)
