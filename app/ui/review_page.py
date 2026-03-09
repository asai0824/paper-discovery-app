import streamlit as st
from ..database import get_db
from ..models.tables import SearchJob, RankingResult
from ..integrations.deepl_client import translate_to_japanese
from ..config import settings


def _get_job_options(db) -> dict:
    jobs = db.query(SearchJob).order_by(SearchJob.created_at.desc()).all()
    return {f"[{j.job_id}] {j.title} ({j.status})": j.job_id for j in jobs}


def _load_ranking_dicts(db, job_id: int, top_n: int) -> list[dict]:
    """セッション内でRankingResultを全てdictに変換して返す。"""
    q = (
        db.query(RankingResult)
        .filter(RankingResult.job_id == job_id)
        .order_by(RankingResult.final_rank)
    )
    if top_n > 0:
        q = q.limit(top_n)
    results = []
    for r in q.all():
        candidate = r.candidate
        paper = candidate.paper if candidate else None
        score = candidate.score if candidate else None
        results.append({
            "rank_id": r.rank_id,
            "final_rank": r.final_rank,
            "decision": r.decision,
            "reason_text": r.reason_text or "",
            "title": paper.title if paper else "",
            "doi": paper.doi or "" if paper else "",
            "doi_url": f"https://doi.org/{paper.doi}" if paper and paper.doi else "",
            "abstract": paper.abstract or "" if paper else "",
            "year": paper.year if paper else None,
            "journal": paper.journal or "" if paper else "",
            "citation_count": paper.citation_count if paper else 0,
            "discovery_path": candidate.discovery_path or "" if candidate else "",
            "total_score": score.total_score if score else 0.0,
            "theme_score": score.theme_score if score else 0.0,
            "method_score": score.method_score if score else 0.0,
            "recency_score": score.recency_score if score else 0.0,
            "impact_score": score.impact_score if score else 0.0,
        })
    return results


def _parse_reason_sections(reason_text: str) -> dict:
    """[Matched] / [Concerns] マーカーでreason_textを3セクションに分解する。"""
    summary, matched, concerns = [], [], []
    current = "summary"
    for line in reason_text.splitlines():
        if line.startswith("[Matched]"):
            current = "matched"
            content = line[len("[Matched]"):].strip()
            if content:
                matched.append(content)
        elif line.startswith("[Concerns]"):
            current = "concerns"
            content = line[len("[Concerns]"):].strip()
            if content:
                concerns.append(content)
        elif line.strip():
            if current == "summary":
                summary.append(line.strip())
            elif current == "matched":
                matched.append(line.strip())
            elif current == "concerns":
                concerns.append(line.strip())
    return {
        "summary": " ".join(summary),
        "matched": " ".join(matched),
        "concerns": " ".join(concerns),
    }



def _save_decision(rank_id: int, decision: str, comment: str):
    with get_db() as db:
        r = db.query(RankingResult).filter(RankingResult.rank_id == rank_id).first()
        if r:
            r.decision = decision
            if comment.strip():
                r.reason_text = comment.strip()


def render():
    st.title("上位論文 確認・判定")
    st.caption("上位候補を1件ずつ確認し、採用・保留・却下の判定を付けます。")

    # ---- ジョブ選択 ----
    with get_db() as db:
        job_options = _get_job_options(db)

    if not job_options:
        st.info("検索ジョブがありません。まず「新規検索」から検索を作成してください。")
        return

    col_job, col_top = st.columns([3, 1])
    with col_job:
        default_key = None
        if "current_job_id" in st.session_state:
            for k, v in job_options.items():
                if v == st.session_state["current_job_id"]:
                    default_key = k
                    break
        selected_label = st.selectbox(
            "検索ジョブを選択",
            list(job_options.keys()),
            index=list(job_options.keys()).index(default_key) if default_key else 0,
        )
    with col_top:
        top_n = st.number_input("上位N件を対象（0=全件）", min_value=0, value=20, step=10)

    job_id = job_options[selected_label]

    # ---- データ読み込み ----
    with get_db() as db:
        all_rows = _load_ranking_dicts(db, job_id, top_n)

    if not all_rows:
        st.warning("候補論文がありません。")
        return

    # ---- フィルタ ----
    filter_decision = st.multiselect(
        "表示する判定状態",
        ["pending", "accepted", "hold", "rejected"],
        default=["pending"],
        help="選択した判定状態の論文のみ表示します（空=全件）",
    )

    filtered = [r for r in all_rows if not filter_decision or r["decision"] in filter_decision]

    if not filtered:
        st.info("該当する論文がありません。フィルタ条件を変更してください。")
        return

    # ---- ナビゲーション状態 ----
    state_key = f"review_idx_{job_id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = 0

    idx = st.session_state[state_key]
    if idx >= len(filtered):
        idx = 0
        st.session_state[state_key] = 0

    # ---- 進捗バー ----
    total = len(filtered)
    decided = sum(1 for r in filtered if r["decision"] != "pending")
    st.progress(
        decided / total if total > 0 else 0,
        text=f"判定済: {decided} / {total} 件",
    )

    # ---- ナビゲーションボタン ----
    nav_col1, nav_col2, nav_col3 = st.columns([1, 4, 1])
    with nav_col1:
        if st.button("◀ 前へ", use_container_width=True, disabled=(idx == 0)):
            st.session_state[state_key] = idx - 1
            st.rerun()
    with nav_col2:
        st.markdown(
            f"<p style='text-align:center; font-size:1rem; margin-top:8px;'>"
            f"<b>{idx + 1}</b> / {total} 件目（Rank: {filtered[idx]['final_rank']}）"
            f"</p>",
            unsafe_allow_html=True,
        )
    with nav_col3:
        if st.button("次へ ▶", use_container_width=True, disabled=(idx == total - 1)):
            st.session_state[state_key] = idx + 1
            st.rerun()

    # ---- 論文カード ----
    row = filtered[idx]
    rank_id = row["rank_id"]

    with st.container(border=True):
        # タイトル
        if row["doi_url"]:
            st.markdown(f"### [{row['title']}]({row['doi_url']})")
        else:
            st.markdown(f"### {row['title']}")

        # メタ情報
        meta_parts = []
        if row["year"]:
            meta_parts.append(f"**{row['year']}年**")
        if row["journal"]:
            meta_parts.append(row["journal"])
        if row["doi"]:
            meta_parts.append(f"DOI: `{row['doi']}`")
        if row["citation_count"]:
            meta_parts.append(f"被引用数: {row['citation_count']}")
        if row["discovery_path"]:
            meta_parts.append(f"収集経路: {row['discovery_path']}")
        st.markdown("  |  ".join(meta_parts))

        st.divider()

        # スコア表示
        s1, s2, s3, s4, s5 = st.columns(5)
        with s1:
            st.metric("Total", f"{row['total_score']:.1f}")
        with s2:
            st.metric("Theme", f"{row['theme_score']:.1f}")
        with s3:
            st.metric("Method", f"{row['method_score']:.1f}")
        with s4:
            st.metric("Recency", f"{row['recency_score']:.1f}")
        with s5:
            st.metric("Impact", f"{row['impact_score']:.1f}")

        # LLM評価（Geminiが日本語で出力済み）
        if row["reason_text"]:
            sections = _parse_reason_sections(row["reason_text"])
            if sections["summary"]:
                st.info(f"**総評:** {sections['summary']}")
            if sections["matched"]:
                st.success(f"**一致手法・材料:** {sections['matched']}")
            if sections["concerns"]:
                st.warning(f"**懸念点:** {sections['concerns']}")

        # アブストラクト（ボタンでDeepL翻訳、キャッシュあり）
        if row["abstract"]:
            with st.expander("アブストラクトを表示", expanded=True):
                st.write(row["abstract"])
                if settings.deepl_api_key:
                    abs_cache_key = f"abs_ja_{rank_id}"
                    if abs_cache_key in st.session_state:
                        st.divider()
                        st.write(st.session_state[abs_cache_key])
                    elif st.button("日本語に翻訳", key=f"translate_abs_{rank_id}"):
                        with st.spinner("翻訳中..."):
                            st.session_state[abs_cache_key] = translate_to_japanese(
                                row["abstract"], settings.deepl_api_key
                            )
                        st.rerun()
        else:
            st.caption("アブストラクトなし（有料論文の可能性あり）")

    # ---- 判定フォーム ----
    st.subheader("判定")
    decision_labels = {
        "accepted": "🟢 採用",
        "hold": "🟡 保留",
        "rejected": "🔴 却下",
        "pending": "⚪ 未判定",
    }
    current_decision = row["decision"]
    st.caption(f"現在の判定: **{decision_labels.get(current_decision, current_decision)}**")

    dec_col1, dec_col2, dec_col3 = st.columns(3)
    with dec_col1:
        accepted = st.button(
            "🟢 採用", use_container_width=True,
            type="primary" if current_decision == "accepted" else "secondary",
        )
    with dec_col2:
        hold = st.button(
            "🟡 保留", use_container_width=True,
            type="primary" if current_decision == "hold" else "secondary",
        )
    with dec_col3:
        rejected = st.button(
            "🔴 却下", use_container_width=True,
            type="primary" if current_decision == "rejected" else "secondary",
        )

    comment = st.text_input("コメント（任意）", placeholder="判定理由やメモを記入")

    new_decision = None
    if accepted:
        new_decision = "accepted"
    elif hold:
        new_decision = "hold"
    elif rejected:
        new_decision = "rejected"

    if new_decision:
        _save_decision(rank_id, new_decision, comment)
        st.success(f"判定を「{decision_labels[new_decision]}」に保存しました。")
        if idx < total - 1:
            st.session_state[state_key] = idx + 1
        st.rerun()

    # ---- 判定サマリー（サイドバー） ----
    with st.sidebar:
        st.divider()
        st.subheader("判定サマリー")
        all_decisions = [r["decision"] for r in all_rows]
        counts = {d: all_decisions.count(d) for d in ["accepted", "hold", "rejected", "pending"]}
        st.markdown(f"🟢 採用: **{counts['accepted']}** 件")
        st.markdown(f"🟡 保留: **{counts['hold']}** 件")
        st.markdown(f"🔴 却下: **{counts['rejected']}** 件")
        st.markdown(f"⚪ 未判定: **{counts['pending']}** 件")
