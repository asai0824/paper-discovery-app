import time
import requests
from dataclasses import dataclass, field
from typing import Optional


OPENALEX_BASE = "https://api.openalex.org"
DEFAULT_EMAIL = "paper-discovery-app@example.com"  # polite pooling用


@dataclass
class PaperData:
    title: str
    doi: Optional[str] = None
    abstract: Optional[str] = None
    year: Optional[int] = None
    journal: Optional[str] = None
    authors: list = field(default_factory=list)
    citation_count: int = 0
    paper_type: Optional[str] = None
    openalex_id: Optional[str] = None
    discovery_path: str = "openalex_search"


def _get(url: str, params: dict) -> dict:
    params["mailto"] = DEFAULT_EMAIL
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == 2:
                raise
            time.sleep(1)
    return {}


def _parse_work(work: dict, discovery_path: str = "openalex_search") -> PaperData:
    doi = work.get("doi")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]

    abstract = None
    abstract_inverted = work.get("abstract_inverted_index")
    if abstract_inverted:
        abstract = _reconstruct_abstract(abstract_inverted)

    journal = None
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    if source:
        journal = source.get("display_name")

    authors = []
    for authorship in work.get("authorships", []):
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append(name)

    paper_type = work.get("type")

    citation_count = work.get("cited_by_count", 0)

    return PaperData(
        title=work.get("display_name", ""),
        doi=doi,
        abstract=abstract,
        year=work.get("publication_year"),
        journal=journal,
        authors=authors,
        citation_count=citation_count,
        paper_type=paper_type,
        openalex_id=work.get("id"),
        discovery_path=discovery_path,
    )


def _reconstruct_abstract(inverted_index: dict) -> str:
    positions = []
    for word, pos_list in inverted_index.items():
        for pos in pos_list:
            positions.append((pos, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in positions)


def search_works(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    max_results: int = 200,
) -> list[PaperData]:
    papers = []
    per_page = 50
    page = 1

    filter_parts = []
    if year_from:
        filter_parts.append(f"publication_year:>{year_from - 1}")
    if year_to:
        filter_parts.append(f"publication_year:<{year_to + 1}")

    while len(papers) < max_results:
        params = {
            "search": query,
            "per-page": per_page,
            "page": page,
            "select": "id,display_name,doi,abstract_inverted_index,publication_year,primary_location,authorships,cited_by_count,type",
        }
        if filter_parts:
            params["filter"] = ",".join(filter_parts)

        data = _get(f"{OPENALEX_BASE}/works", params)
        results = data.get("results", [])
        if not results:
            break

        for work in results:
            papers.append(_parse_work(work, "openalex_search"))

        meta = data.get("meta", {})
        total = meta.get("count", 0)
        if len(papers) >= total or len(papers) >= max_results:
            break

        page += 1
        time.sleep(0.1)

    return papers[:max_results]


def get_work_by_doi(doi: str) -> PaperData | None:
    """DOIから1件の論文データを取得する。"""
    try:
        data = _get(
            f"{OPENALEX_BASE}/works/https://doi.org/{doi}",
            {"select": "id,display_name,doi,abstract_inverted_index,publication_year,primary_location,authorships,cited_by_count,type"},
        )
        if data and data.get("display_name"):
            return _parse_work(data, "doi_list")
    except Exception:
        pass
    return None


def get_related_works(openalex_id: str, max_results: int = 30) -> list[PaperData]:
    clean_id = openalex_id.split("/")[-1]
    papers = []

    # 被引用論文（citing works）
    try:
        params = {
            "filter": f"cites:{clean_id}",
            "per-page": max_results,
            "select": "id,display_name,doi,abstract_inverted_index,publication_year,primary_location,authorships,cited_by_count,type",
        }
        data = _get(f"{OPENALEX_BASE}/works", params)
        for work in data.get("results", []):
            papers.append(_parse_work(work, "openalex_citing"))
    except Exception:
        pass

    # related works
    try:
        data = _get(f"{OPENALEX_BASE}/works/{clean_id}", {
            "select": "related_works"
        })
        related_ids = data.get("related_works", [])[:10]
        for rid in related_ids:
            rid_clean = rid.split("/")[-1]
            try:
                work_data = _get(f"{OPENALEX_BASE}/works/{rid_clean}", {
                    "select": "id,display_name,doi,abstract_inverted_index,publication_year,primary_location,authorships,cited_by_count,type"
                })
                papers.append(_parse_work(work_data, "openalex_related"))
                time.sleep(0.05)
            except Exception:
                continue
    except Exception:
        pass

    return papers
