import time
import requests
from typing import Optional

from .openalex_client import PaperData


S2_BASE = "https://api.semanticscholar.org/graph/v1"

_FIELDS = "paperId,title,abstract,year,venue,authors,citationCount,externalIds,publicationTypes"


def _get(url: str, params: dict, api_key: str | None = None) -> dict:
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2)
    return {}


def _parse_paper(item: dict, discovery_path: str = "s2_search") -> PaperData | None:
    title = item.get("title", "").strip()
    if not title:
        return None

    doi = (item.get("externalIds") or {}).get("DOI")

    authors = [a["name"] for a in item.get("authors", []) if a.get("name")]

    pub_types = item.get("publicationTypes") or []
    paper_type = pub_types[0].lower() if pub_types else None

    return PaperData(
        title=title,
        doi=doi,
        abstract=item.get("abstract"),
        year=item.get("year"),
        journal=item.get("venue") or None,
        authors=authors,
        citation_count=item.get("citationCount") or 0,
        paper_type=paper_type,
        openalex_id=None,
        discovery_path=discovery_path,
    )


def search_papers(
    query: str,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    max_results: int = 100,
    api_key: str | None = None,
) -> list[PaperData]:
    papers = []
    per_page = min(100, max_results)
    offset = 0

    while len(papers) < max_results:
        params: dict = {
            "query": query,
            "fields": _FIELDS,
            "limit": per_page,
            "offset": offset,
        }
        if year_from or year_to:
            yf = str(year_from) if year_from else ""
            yt = str(year_to) if year_to else ""
            params["year"] = f"{yf}-{yt}"

        try:
            data = _get(f"{S2_BASE}/paper/search", params, api_key)
        except Exception:
            break

        items = data.get("data", [])
        if not items:
            break

        for item in items:
            pd = _parse_paper(item, "s2_search")
            if pd:
                papers.append(pd)

        total = data.get("total", 0)
        offset += per_page
        if offset >= total or len(papers) >= max_results:
            break

        time.sleep(0.5)

    return papers[:max_results]


def get_references(
    s2_paper_id: str,
    max_results: int = 30,
    api_key: str | None = None,
) -> list[PaperData]:
    """論文の参考文献リストを取得する。"""
    papers = []
    try:
        data = _get(
            f"{S2_BASE}/paper/{s2_paper_id}/references",
            {"fields": _FIELDS, "limit": max_results},
            api_key,
        )
        for ref in data.get("data", []):
            cited = ref.get("citedPaper") or {}
            pd = _parse_paper(cited, "s2_reference")
            if pd:
                papers.append(pd)
    except Exception:
        pass
    return papers


def get_citations(
    s2_paper_id: str,
    max_results: int = 30,
    api_key: str | None = None,
) -> list[PaperData]:
    """論文を引用している論文リストを取得する（=被引用論文）。"""
    papers = []
    try:
        data = _get(
            f"{S2_BASE}/paper/{s2_paper_id}/citations",
            {"fields": _FIELDS, "limit": max_results},
            api_key,
        )
        for ref in data.get("data", []):
            citing = ref.get("citingPaper") or {}
            pd = _parse_paper(citing, "s2_citation")
            if pd:
                papers.append(pd)
    except Exception:
        pass
    return papers


def get_recommendations(
    positive_ids: list[str],
    max_results: int = 50,
    api_key: str | None = None,
) -> list[PaperData]:
    """
    Semantic Scholar Recommendations APIで類似論文を取得する。
    positive_idsはS2のpaper_idリスト（最大5件）。
    """
    if not positive_ids:
        return []
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    try:
        resp = requests.post(
            "https://api.semanticscholar.org/recommendations/v1/papers/",
            headers=headers,
            params={"fields": _FIELDS, "limit": min(max_results, 500)},
            json={"positivePaperIds": positive_ids[:5], "negativePaperIds": []},
            timeout=30,
        )
        if resp.status_code == 429:
            time.sleep(5)
            resp = requests.post(
                "https://api.semanticscholar.org/recommendations/v1/papers/",
                headers=headers,
                params={"fields": _FIELDS, "limit": min(max_results, 500)},
                json={"positivePaperIds": positive_ids[:5], "negativePaperIds": []},
                timeout=30,
            )
        resp.raise_for_status()
        papers = []
        for item in resp.json().get("recommendedPapers", []):
            pd = _parse_paper(item, "s2_recommendation")
            if pd:
                papers.append(pd)
        return papers
    except Exception:
        return []


def find_paper_by_doi(doi: str, api_key: str | None = None) -> str | None:
    """DOIからSemantic Scholar の paper_id を取得する。"""
    try:
        data = _get(
            f"{S2_BASE}/paper/DOI:{doi}",
            {"fields": "paperId"},
            api_key,
        )
        return data.get("paperId")
    except Exception:
        return None
