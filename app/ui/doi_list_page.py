import re
import time
import streamlit as st
from ..database import get_db
from ..services.search_service import create_search_job, run_doi_list_collection
from ..services.scoring_service import score_all, rescore_top_with_llm
from ..integrations.deepl_client import translate_to_english
from ..config import settings
from .search_page import _extract_doi


def render():
    st.title("マイリスト採点")
    st.caption("手持ちのDOI・URLリストをテーマに対して採点・順位付けします。")

    with st.form("doi_list_form"):
        job_title = st.text_input(
            "ジョブタイトル",
            placeholder="例: 20250310_mylist_perovskite",
        )
        theme_text = st.text_area(
            "テーマ文（採点基準）*",
            placeholder="例: ペロブスカイト量子ドットの合成とLED応用",
            height=80,
        )
        doi_list_text = st.text_area(
            "DOI / URL リスト（1行1件）*",
            placeholder=(
                "例:\n"
                "10.1021/jacs.3c00001\n"
                "https://doi.org/10.1039/D3NR00001A\n"
                "https://arxiv.org/abs/2301.12345"
            ),
            height=200,
            help="DOI、doi.orgのURL、出版社URL、arXiv URLに対応しています。",
        )

        with st.expander("詳細設定（任意）"):
            min_score_for_llm = st.slider(
                "LLM採点スキップ閾値",
                min_value=0, max_value=80, value=0, step=5,
                help="マイリスト採点では全件採点したいことが多いため、デフォルト0（全件）。",
            )

        submitted = st.form_submit_button("採点開始", type="primary", use_container_width=True)

    if submitted:
        if not theme_text.strip():
            st.error("テーマ文は必須です。")
            return
        if not job_title.strip():
            st.error("ジョブタイトルは必須です。")
            return

        # DOI抽出
        raw_lines = [l.strip() for l in doi_list_text.splitlines() if l.strip()]
        dois = [_extract_doi(l) for l in raw_lines]
        doi_results = list(zip(raw_lines, dois))  # (元入力, 抽出結果)
        valid_dois = [d for d in dois if d]

        if not valid_dois:
            st.error("有効なDOIまたはURLが見つかりませんでした。")
            return

        # DOI解析結果を表示
        failed = [(orig, doi) for orig, doi in doi_results if doi is None]
        if failed:
            st.warning(f"{len(failed)} 件はDOIを抽出できませんでした: {', '.join(o for o, _ in failed[:3])}")

        # テーマ翻訳
        theme_text_en = theme_text.strip()
        if settings.deepl_api_key:
            with st.spinner("テーマ文を英語に翻訳中..."):
                theme_text_en = translate_to_english(theme_text.strip(), settings.deepl_api_key)
        elif re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", theme_text):
            st.warning("DeepL APIキーが未設定のため翻訳できません。英語で入力してください。")

        if theme_text_en != theme_text.strip():
            st.info(f"翻訳クエリ: **{theme_text_en}**")

        try:
            with get_db() as db:
                job = create_search_job(
                    db=db,
                    title=job_title.strip(),
                    theme_text=theme_text.strip(),
                    year_from=None,
                    year_to=None,
                    include_terms=None,
                    exclude_terms=None,
                    subject_tags=None,
                    seed_dois=[],
                    max_candidates=len(valid_dois),
                    use_abstract=True,
                )
                job.query.theme_text = theme_text_en

                # 論文データ取得（進捗表示）
                fetch_bar = st.progress(0, text=f"論文データ取得中... (0/{len(valid_dois)})")
                fetch_results = []

                def on_fetch_progress(current, total, doi, success):
                    fetch_bar.progress(
                        current / total,
                        text=f"論文データ取得中... ({current}/{total}): {doi[:40]}",
                    )
                    fetch_results.append((doi, success))

                candidates = run_doi_list_collection(
                    db=db,
                    job=job,
                    dois=valid_dois,
                    progress_callback=on_fetch_progress,
                )
                fetch_bar.empty()

                not_found = [doi for doi, ok in fetch_results if not ok]
                if not_found:
                    st.warning(f"{len(not_found)} 件はデータを取得できませんでした: {', '.join(not_found[:3])}")

                st.info(f"{len(candidates)} 件を取得しました。採点中...")

                score_all(
                    db=db,
                    job_id=job.job_id,
                    query_text=theme_text_en,
                    include_terms=None,
                    use_abstract=True,
                )

                # LLM採点
                if settings.gemini_api_key:
                    progress_bar = st.progress(0, text="Geminiで採点中... (0/?)")
                    eta_text = st.empty()
                    start_time = time.time()

                    def on_llm_progress(current: int, total: int):
                        pct = current / total if total > 0 else 1.0
                        progress_bar.progress(pct, text=f"Geminiで採点中... ({current}/{total})")
                        elapsed = time.time() - start_time
                        if current > 0:
                            remaining = (elapsed / current) * (total - current)
                            if remaining > 1:
                                eta_text.caption(f"残り約 {int(remaining)} 秒")
                            else:
                                eta_text.empty()

                    n_llm = rescore_top_with_llm(
                        db=db,
                        job_id=job.job_id,
                        query_text=theme_text_en,
                        api_key=settings.gemini_api_key,
                        top_n=len(candidates),
                        min_score_for_llm=float(min_score_for_llm),
                        progress_callback=on_llm_progress,
                    )
                    progress_bar.empty()
                    eta_text.empty()
                    st.info(f"Gemini採点完了（{n_llm} 件）")

                saved_job_id = job.job_id

            st.success(f"完了しました！{len(candidates)} 件を採点しました。")
            st.session_state["current_job_id"] = saved_job_id
            st.session_state["page"] = "候補一覧"
            st.rerun()

        except Exception as e:
            st.error(f"エラーが発生しました: {e}")
            raise
