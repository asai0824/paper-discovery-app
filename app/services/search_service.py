from datetime import datetime
from sqlalchemy.orm import Session

from ..integrations.openalex_client import search_works, get_related_works, get_work_by_doi, PaperData
from ..integrations.semantic_scholar_client import (
    search_papers as s2_search_papers,
    get_references as s2_get_references,
    get_citations as s2_get_citations,
    get_recommendations as s2_get_recommendations,
    find_paper_by_doi as s2_find_by_doi,
)
from ..integrations.gemini_client import expand_search_queries
from ..models.tables import (
    SearchJob, SearchQuery, SeedPaper, Paper, PaperCandidate, ApiLog
)


def create_search_job(
    db: Session,
    title: str,
    theme_text: str,
    year_from: int | None,
    year_to: int | None,
    include_terms: str | None,
    exclude_terms: str | None,
    subject_tags: str | None,
    seed_dois: list[str],
    max_candidates: int = 200,
    use_abstract: bool = True,
    created_by: int | None = None,
) -> SearchJob:
    job = SearchJob(title=title, status="running", created_by=created_by)
    db.add(job)
    db.flush()

    query = SearchQuery(
        job_id=job.job_id,
        theme_text=theme_text,
        year_from=year_from,
        year_to=year_to,
        include_terms=include_terms,
        exclude_terms=exclude_terms,
        subject_tags=subject_tags,
        max_candidates=max_candidates,
        use_abstract=use_abstract,
    )
    db.add(query)

    for doi in seed_dois:
        seed = SeedPaper(job_id=job.job_id, doi=doi.strip())
        db.add(seed)

    db.flush()
    return job


def run_collection(
    db: Session,
    job: SearchJob,
    use_semantic_scholar: bool = False,
    s2_api_key: str | None = None,
    gemini_api_key: str | None = None,
) -> list[PaperCandidate]:
    query = job.query

    # ① クエリ拡張（Gemini）
    queries = _expand_queries(query.theme_text, gemini_api_key)

    # ② 各クエリでOpenAlex検索
    papers: list[PaperData] = []
    per_query_max = max(50, query.max_candidates // len(queries))
    for q in queries:
        papers += _collect_openalex(db, job, query, search_query=q, max_results=per_query_max)

    # ③ Semantic Scholar検索（オプション）
    if use_semantic_scholar:
        s2_per_query = max(30, query.max_candidates // (len(queries) * 2))
        for q in queries[:2]:  # S2はAPIレート制限のため先頭2クエリのみ
            papers += _collect_semantic_scholar(db, job, query, s2_per_query, s2_api_key, search_query=q)

    # ④ Seed論文から拡張（参考文献・被引用・S2推薦）
    if job.seed_papers:
        papers += _expand_from_seeds(db, job, job.seed_papers, use_semantic_scholar, s2_api_key)

    # ⑤ 重複除去して保存
    candidates = _dedupe_and_save(db, job, papers)

    job.status = "scored"
    job.completed_at = datetime.utcnow()
    db.flush()

    return candidates


def run_doi_list_collection(
    db: Session,
    job: SearchJob,
    dois: list[str],
    progress_callback=None,
) -> list[PaperCandidate]:
    """
    DOIリストから論文データを取得してJobに候補として追加する。
    progress_callback: (current, total, doi, success) を受け取る関数（省略可）
    """
    papers: list[PaperData] = []
    total = len(dois)

    for i, doi in enumerate(dois, start=1):
        paper_data = get_work_by_doi(doi)
        success = paper_data is not None
        if success:
            papers.append(paper_data)
        if progress_callback:
            progress_callback(i, total, doi, success)

    candidates = _dedupe_and_save(db, job, papers)
    job.status = "scored"
    job.completed_at = datetime.utcnow()
    db.flush()
    return candidates


def _expand_queries(theme_text: str, gemini_api_key: str | None) -> list[str]:
    """
    Geminiでクエリ拡張。失敗時は元クエリのみ返す。
    日本語が含まれている場合は、Geminiが生成した英語クエリのみを使用し
    日本語の原文はOpenAlex/S2に送らない。
    """
    import re
    has_japanese = bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", theme_text))

    if not gemini_api_key:
        # Geminiなし・日本語あり → 英語検索できないが仕方なく原文で試みる
        return [theme_text]

    try:
        all_queries = expand_search_queries(theme_text, gemini_api_key, n_variants=3)
        if has_japanese:
            # 日本語原文を除いた英語クエリのみ返す
            english_only = [q for q in all_queries if q != theme_text]
            return english_only if english_only else [theme_text]
        return all_queries
    except Exception:
        return [theme_text]


def _collect_openalex(
    db: Session,
    job: SearchJob,
    query: SearchQuery,
    search_query: str | None = None,
    max_results: int | None = None,
) -> list[PaperData]:
    q = search_query or query.theme_text
    n = max_results or query.max_candidates
    try:
        papers = search_works(
            query=q,
            year_from=query.year_from,
            year_to=query.year_to,
            max_results=n,
        )
        _log_api(db, job.job_id, "openalex", "/works", f"search: {q}", 200)
        return papers
    except Exception as e:
        _log_api(db, job.job_id, "openalex", "/works", str(e), 500)
        return []


def _collect_semantic_scholar(
    db: Session,
    job: SearchJob,
    query: SearchQuery,
    max_results: int,
    api_key: str | None,
    search_query: str | None = None,
) -> list[PaperData]:
    q = search_query or query.theme_text
    try:
        papers = s2_search_papers(
            query=q,
            year_from=query.year_from,
            year_to=query.year_to,
            max_results=max_results,
            api_key=api_key or None,
        )
        _log_api(db, job.job_id, "semantic_scholar", "/paper/search", f"search: {q}", 200)
        return papers
    except Exception as e:
        _log_api(db, job.job_id, "semantic_scholar", "/paper/search", str(e), 500)
        return []


def _expand_from_seeds(
    db: Session,
    job: SearchJob,
    seeds,
    use_semantic_scholar: bool = False,
    s2_api_key: str | None = None,
) -> list[PaperData]:
    expanded: list[PaperData] = []
    s2_ids: list[str] = []

    for seed in seeds:
        if not seed.doi:
            continue

        # OpenAlex: 被引用論文 + related works
        try:
            import requests as _requests
            resp = _requests.get(
                f"https://api.openalex.org/works/https://doi.org/{seed.doi}",
                params={"mailto": "paper-discovery-app@example.com", "select": "id"},
                timeout=15,
            )
            if resp.ok:
                openalex_id = resp.json().get("id", "")
                if openalex_id:
                    related = get_related_works(openalex_id, max_results=20)
                    expanded.extend(related)
                    _log_api(db, job.job_id, "openalex", "/works/related", f"seed doi: {seed.doi}", 200)
        except Exception as e:
            _log_api(db, job.job_id, "openalex", "/works/related", str(e), 500)

        # S2: references + citations + recommendations
        if use_semantic_scholar:
            try:
                s2_id = s2_find_by_doi(seed.doi, s2_api_key or None)
                if s2_id:
                    s2_ids.append(s2_id)

                    # 参考文献（seed が引用している論文）
                    refs = s2_get_references(s2_id, max_results=20, api_key=s2_api_key or None)
                    expanded.extend(refs)
                    _log_api(db, job.job_id, "semantic_scholar", "/paper/references", f"seed doi: {seed.doi}", 200)

                    # 被引用論文（seed を引用している論文）
                    cites = s2_get_citations(s2_id, max_results=20, api_key=s2_api_key or None)
                    expanded.extend(cites)
                    _log_api(db, job.job_id, "semantic_scholar", "/paper/citations", f"seed doi: {seed.doi}", 200)
            except Exception as e:
                _log_api(db, job.job_id, "semantic_scholar", "/paper/references", str(e), 500)

    # S2 Recommendations（seed全体に対してまとめて推薦を取得）
    if use_semantic_scholar and s2_ids:
        try:
            recs = s2_get_recommendations(s2_ids, max_results=50, api_key=s2_api_key or None)
            expanded.extend(recs)
            _log_api(db, job.job_id, "semantic_scholar", "/recommendations", f"seeds: {len(s2_ids)}", 200)
        except Exception as e:
            _log_api(db, job.job_id, "semantic_scholar", "/recommendations", str(e), 500)

    return expanded


def _dedupe_and_save(db: Session, job: SearchJob, papers: list[PaperData]) -> list[PaperCandidate]:
    seen_dois: dict[str, Paper] = {}
    seen_titles: dict[str, Paper] = {}
    candidates = []

    # 既存のDOIをDBから取得してキャッシュ
    existing_papers: dict[str, Paper] = {}
    for p in db.query(Paper).filter(Paper.doi.isnot(None)).all():
        if p.doi:
            existing_papers[p.doi.lower()] = p

    for paper_data in papers:
        doi_key = paper_data.doi.lower() if paper_data.doi else None
        title_key = _normalize_title(paper_data.title)

        # 同一jobで重複チェック
        if doi_key and doi_key in seen_dois:
            continue
        if not doi_key and title_key in seen_titles:
            continue

        # paperマスタから探す or 作成
        if doi_key and doi_key in existing_papers:
            paper = existing_papers[doi_key]
            if paper_data.citation_count > paper.citation_count:
                paper.citation_count = paper_data.citation_count
        else:
            paper = Paper(
                doi=paper_data.doi,
                title=paper_data.title,
                abstract=paper_data.abstract,
                year=paper_data.year,
                journal=paper_data.journal,
                authors_json=paper_data.authors,
                citation_count=paper_data.citation_count,
                paper_type=paper_data.paper_type,
                openalex_id=paper_data.openalex_id,
                source_primary="openalex",
            )
            db.add(paper)
            db.flush()
            if doi_key:
                existing_papers[doi_key] = paper

        candidate = PaperCandidate(
            job_id=job.job_id,
            paper_id=paper.paper_id,
            discovery_path=paper_data.discovery_path,
            is_deduped=True,
        )
        db.add(candidate)
        db.flush()
        candidates.append(candidate)

        if doi_key:
            seen_dois[doi_key] = paper
        seen_titles[title_key] = paper

    return candidates


def _normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def _log_api(db: Session, job_id: int, provider: str, endpoint: str, summary: str, status: int):
    log = ApiLog(
        job_id=job_id,
        provider=provider,
        endpoint=endpoint,
        request_summary=summary,
        status_code=status,
    )
    db.add(log)
