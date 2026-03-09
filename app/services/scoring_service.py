import math
from datetime import datetime
from sqlalchemy.orm import Session
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ..models.tables import PaperCandidate, Paper, ScoreDetail, RankingResult
from ..config import scoring_config
from ..integrations.gemini_client import score_method_relevance

# sentence-transformers を遅延ロード（起動時間への影響を最小化）
# Streamlit環境では @st.cache_resource、それ以外ではグローバル変数でキャッシュ
_embed_model_cache = {}

def _get_embed_model():
    import os
    if os.environ.get("DISABLE_EMBEDDING", "").lower() == "true":
        return None
    if "model" not in _embed_model_cache:
        try:
            from sentence_transformers import SentenceTransformer
            _embed_model_cache["model"] = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            _embed_model_cache["model"] = None
    return _embed_model_cache.get("model")


def _calc_embed_scores(query_text: str, corpus: list[str]) -> list[float]:
    """クエリと各論文テキストのembedding cosine類似度を計算して0-1に正規化する。"""
    if not corpus:
        return []
    model = _get_embed_model()
    if model is None:
        return [0.0] * len(corpus)
    try:
        texts = [query_text] + corpus
        embeddings = model.encode(texts, batch_size=64, show_progress_bar=False, normalize_embeddings=True)
        query_vec = embeddings[0:1]
        paper_vecs = embeddings[1:]
        scores = cosine_similarity(query_vec, paper_vecs)[0]
        # cosine similarity は -1〜1 だが正規化済みベクトルは 0〜1 に近い
        scores = scores.clip(0, 1)
        max_score = scores.max() if scores.max() > 0 else 1.0
        return (scores / max_score).tolist()
    except Exception:
        return [0.0] * len(corpus)

SCORING_VERSION = scoring_config.get("scoring_version", "1.0")
CFG = scoring_config.get("scoring", {})

# 手法辞書（化学・材料分野）
METHOD_DICT = {
    "synthesis": {
        "hot injection": 0.5, "larp": 0.5, "microfluidic": 0.5,
        "flow synthesis": 0.5, "solvothermal": 0.5, "hydrothermal": 0.5,
        "sol-gel": 0.4, "coprecipitation": 0.4, "electrodeposition": 0.4,
        "chemical vapor deposition": 0.5, "cvd": 0.4, "pvd": 0.4,
        "atomic layer deposition": 0.5, "ald": 0.4,
    },
    "material": {
        "perovskite": 0.3, "quantum dot": 0.3, "nanoparticle": 0.3,
        "polymer composite": 0.3, "metal organic framework": 0.3, "mof": 0.3,
        "graphene": 0.3, "carbon nanotube": 0.3, "zeolite": 0.3,
        "cspbbr3": 0.3, "halide perovskite": 0.3,
    },
    "characterization": {
        "xrd": 0.2, "tem": 0.2, "sem": 0.2, "nmr": 0.2,
        "ftir": 0.2, "raman": 0.2, "xps": 0.2, "pl": 0.2,
        "absorption": 0.2, "photoluminescence": 0.2, "uv-vis": 0.2,
    },
}

REVIEW_KEYWORDS = ["review", "progress", "perspective", "advances in", "overview"]
METHOD_PAPER_KEYWORDS = ["method", "protocol", "procedure", "synthesis of", "preparation of"]
SEMINAL_THRESHOLD = 500


def score_all(
    db: Session,
    job_id: int,
    query_text: str,
    include_terms: str | None,
    use_abstract: bool = True,
) -> list[RankingResult]:
    candidates = (
        db.query(PaperCandidate)
        .filter(PaperCandidate.job_id == job_id)
        .all()
    )

    include_list = [t.strip().lower() for t in (include_terms or "").split(",") if t.strip()]
    current_year = datetime.now().year

    citation_counts = [c.paper.citation_count for c in candidates if c.paper]
    p95_citation = _percentile(citation_counts, 95) if citation_counts else 1

    # use_abstract=False のときはタイトルのみでコーパスを構築 → 全論文を同じ土俵に
    valid_candidates = [c for c in candidates if c.paper]
    corpus = [_build_paper_text(c.paper, use_abstract) for c in valid_candidates]
    tfidf_scores = _calc_tfidf_scores(query_text, corpus)
    embed_scores = _calc_embed_scores(query_text, corpus)

    rankings = []
    for i, candidate in enumerate(valid_candidates):
        paper = candidate.paper

        paper_text = _build_paper_text(paper, use_abstract)
        tfidf_norm = tfidf_scores[i] if i < len(tfidf_scores) else 0.0
        embed_norm = embed_scores[i] if i < len(embed_scores) else 0.0

        # --- テーマ一致度 ---
        theme_text_score = CFG.get("theme", {}).get("text_weight", 10) * tfidf_norm
        theme_keyword_score = _calc_keyword_score(include_list, paper_text)
        theme_embed_score = CFG.get("theme", {}).get("embed_weight", 20) * embed_norm
        theme_score = theme_text_score + theme_keyword_score + theme_embed_score

        # --- 手法一致度（ルールベース）---
        method_rule_norm = _calc_method_rule(paper_text)
        method_rule_score = CFG.get("method", {}).get("rule_weight", 10) * method_rule_norm
        method_score = method_rule_score
        method_llm_score = 0.0

        # --- 新しさ ---
        recency_score = _calc_recency(paper.year, current_year)

        # --- 影響度 ---
        impact_score = _calc_impact(paper.citation_count, p95_citation)

        # --- 読みやすさ ---
        readability_score = _calc_readability(paper, use_abstract)

        # --- 役割補正 ---
        role_bonus = _calc_role_bonus(paper)

        total_score = (
            theme_score + method_score + recency_score +
            impact_score + readability_score + role_bonus
        )

        score_detail = ScoreDetail(
            candidate_id=candidate.candidate_id,
            theme_score=round(theme_score, 2),
            theme_embed_score=round(theme_embed_score, 2),
            theme_text_score=round(theme_text_score, 2),
            theme_keyword_score=round(theme_keyword_score, 2),
            method_score=round(method_score, 2),
            method_rule_score=round(method_rule_score, 2),
            method_llm_score=round(method_llm_score, 2),
            recency_score=round(recency_score, 2),
            impact_score=round(impact_score, 2),
            readability_score=round(readability_score, 2),
            role_bonus=round(role_bonus, 2),
            total_score=round(total_score, 2),
            scoring_version=SCORING_VERSION,
        )
        db.add(score_detail)
        db.flush()

        ranking = RankingResult(
            job_id=job_id,
            candidate_id=candidate.candidate_id,
            decision="pending",
        )
        db.add(ranking)
        rankings.append((ranking, total_score))

    db.flush()

    rankings.sort(key=lambda x: x[1], reverse=True)
    for rank_num, (ranking, _) in enumerate(rankings, start=1):
        ranking.final_rank = rank_num

    db.flush()
    return [r for r, _ in rankings]


def rescore_top_with_llm(
    db: Session,
    job_id: int,
    query_text: str,
    api_key: str,
    top_n: int | None = None,
    min_score_for_llm: float = 50.0,
    only_unscored: bool = False,
    progress_callback=None,
) -> int:
    """
    上位N件にGeminiで手法一致度を補正し、reason_textを生成する。

    Args:
        min_score_for_llm: これ未満のtotal_scoreの論文はLLM採点をスキップ
        only_unscored: Trueの場合、既にLLM採点済み（method_llm_score > 0）の論文はスキップ
        progress_callback: (current, total) を受け取る関数（省略可）
    Returns:
        処理した件数
    """
    if not api_key:
        return 0

    if top_n is None:
        top_n = scoring_config.get("llm", {}).get("enabled_for_top_n", 30)
    llm_weight = CFG.get("method", {}).get("llm_weight", 10)

    rankings = (
        db.query(RankingResult)
        .filter(RankingResult.job_id == job_id)
        .order_by(RankingResult.final_rank)
        .limit(top_n)
        .all()
    )

    # スコア下限フィルタ後の対象リストを確定
    targets = []
    for ranking in rankings:
        candidate = ranking.candidate
        if not candidate:
            continue
        score_detail = candidate.score
        if not score_detail:
            continue
        if score_detail.total_score < min_score_for_llm:
            continue
        if only_unscored and score_detail.method_llm_score > 0:
            continue
        targets.append(ranking)

    total = len(targets)
    processed = 0

    for ranking in targets:
        candidate = ranking.candidate
        paper = candidate.paper
        score_detail = candidate.score

        result = score_method_relevance(
            query_text=query_text,
            title=paper.title or "",
            abstract=paper.abstract,
            api_key=api_key,
        )
        llm_score = round(llm_weight * result["method_relevance_score"], 2)
        parts = []
        if result.get("summary"):
            parts.append(result["summary"])
        if result.get("matched_methods") and result["matched_methods"] not in ("None", "なし", ""):
            parts.append(f"[Matched] {result['matched_methods']}")
        if result.get("concerns") and result["concerns"] not in ("None", "なし", ""):
            parts.append(f"[Concerns] {result['concerns']}")
        reason_text = "\n".join(parts) if parts else None

        score_detail.method_llm_score = round(llm_score, 2)
        score_detail.method_score = round(score_detail.method_rule_score + llm_score, 2)
        score_detail.total_score = round(
            score_detail.theme_score
            + score_detail.method_score
            + score_detail.recency_score
            + score_detail.impact_score
            + score_detail.readability_score
            + score_detail.role_bonus,
            2,
        )
        if reason_text:
            ranking.reason_text = reason_text

        processed += 1
        if progress_callback:
            progress_callback(processed, total)

    db.flush()

    # 上位N件を再ランク付け（LLMスコアで順位が変わる可能性があるため）
    all_rankings = (
        db.query(RankingResult)
        .filter(RankingResult.job_id == job_id)
        .all()
    )
    all_rankings.sort(
        key=lambda r: r.candidate.score.total_score if r.candidate and r.candidate.score else 0,
        reverse=True,
    )
    for rank_num, r in enumerate(all_rankings, start=1):
        r.final_rank = rank_num

    db.flush()
    return processed


def _build_paper_text(paper: Paper, use_abstract: bool = True) -> str:
    parts = [paper.title or ""]
    if use_abstract and paper.abstract:
        parts.append(paper.abstract)
    if paper.journal:
        parts.append(paper.journal)
    return " ".join(parts).lower()


def _calc_tfidf_scores(query_text: str, corpus: list[str]) -> list[float]:
    if not corpus:
        return []
    try:
        vectorizer = TfidfVectorizer(max_features=5000, stop_words="english")
        tfidf_matrix = vectorizer.fit_transform(corpus)
        query_vec = vectorizer.transform([query_text.lower()])
        scores = cosine_similarity(query_vec, tfidf_matrix)[0]
        max_score = scores.max() if scores.max() > 0 else 1.0
        return (scores / max_score).tolist()
    except Exception:
        return [0.0] * len(corpus)


def _calc_keyword_score(include_terms: list[str], paper_text: str) -> float:
    if not include_terms:
        return 0.0
    matched = sum(1 for term in include_terms if term in paper_text)
    ratio = matched / len(include_terms)
    return CFG.get("theme", {}).get("keyword_weight", 5) * ratio


def _calc_method_rule(paper_text: str) -> float:
    total_weight = 0.0
    matched_weight = 0.0
    for category, terms in METHOD_DICT.items():
        for term, weight in terms.items():
            total_weight += weight
            if term in paper_text:
                matched_weight += weight
    if total_weight == 0:
        return 0.0
    return min(matched_weight / total_weight * 3, 1.0)


def _calc_recency(paper_year: int | None, current_year: int) -> float:
    weight = CFG.get("recency_weight", 15)
    if not paper_year:
        return weight * 0.2
    age = current_year - paper_year
    thresholds = scoring_config.get("scoring", {}).get("recency_thresholds", [])
    for t in thresholds:
        if age <= t["max_age"]:
            return weight * t["norm"]
    return weight * 0.2


def _calc_impact(citation_count: int, p95: float) -> float:
    weight = CFG.get("impact_weight", 15)
    if p95 <= 0:
        return 0.0
    norm = math.log(1 + citation_count) / math.log(1 + p95)
    return weight * min(norm, 1.0)


def _calc_readability(paper: Paper, use_abstract: bool = True) -> float:
    score = 0.0
    # use_abstract=True のときだけ abstract 有無を評価。
    # False のときは abstract を考慮しない（有料論文を不利にしない）
    if use_abstract and paper.abstract:
        score += 3
    title = paper.title or ""
    if 10 <= len(title) <= 200:
        score += 2
    paper_type = (paper.paper_type or "").lower()
    if any(k in paper_type for k in ["review", "method"]):
        score += 2
    if paper.journal and paper.year and paper.authors_json:
        score += 1
    # use_abstract=False のとき上限を7点に下げて、abstract加点3点の穴を埋めない
    max_score = CFG.get("readability_weight", 10) if use_abstract else 7
    return min(score, max_score)


def _calc_role_bonus(paper: Paper) -> float:
    bonus = 0.0
    # role_bonus はタイトルのみでも判定できるキーワードに限る
    title_abstract = ((paper.title or "") + " " + (paper.abstract or "")).lower()

    if any(k in title_abstract for k in REVIEW_KEYWORDS):
        bonus += 2
    if any(k in title_abstract for k in METHOD_PAPER_KEYWORDS):
        bonus += 2
    if paper.citation_count >= SEMINAL_THRESHOLD:
        bonus += 3

    return min(bonus, CFG.get("role_bonus_weight", 5))


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return 1.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return float(sorted_data[min(idx, len(sorted_data) - 1)])
