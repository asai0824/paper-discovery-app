import re
import time
import streamlit as st
from ..database import get_db
from ..services.search_service import create_search_job, run_collection
from ..services.scoring_service import score_all, rescore_top_with_llm
from ..integrations.deepl_client import translate_to_english, translate_terms
from ..config import settings


def render():
    st.title("新規検索作成")
    st.caption("テーマ文を入力して論文候補を収集・採点します。日本語でも入力できます。")

    with st.form("search_form"):
        st.subheader("基本設定")
        job_title = st.text_input(
            "検索タイトル",
            placeholder="例: 20250306_perovskite_synthesis_yamada",
            help="YYYYMMDD_テーマ_作成者 の形式を推奨"
        )
        theme_text = st.text_area(
            "テーマ文 *",
            placeholder="例: ペロブスカイト量子ドットの合成とLED応用\n   または: perovskite quantum dot synthesis for LED applications",
            height=100,
        )

        col1, col2 = st.columns(2)
        with col1:
            year_from = st.number_input("年（From）", min_value=1950, max_value=2030, value=2015, step=1)
        with col2:
            year_to = st.number_input("年（To）", min_value=1950, max_value=2030, value=2026, step=1)

        with st.expander("詳細設定（任意）"):
            include_terms = st.text_input(
                "含むキーワード（カンマ区切り、日本語可）",
                placeholder="例: ペロブスカイト, 量子ドット, フォトルミネッセンス"
            )
            exclude_terms = st.text_input(
                "除外キーワード（カンマ区切り）",
                placeholder="例: silicon, organic"
            )
            subject_tags = st.text_input(
                "分野タグ（カンマ区切り）",
                placeholder="例: materials science, chemistry"
            )
            seed_dois_text = st.text_area(
                "Seed論文 DOI / URL（1行1件、最大3件）",
                placeholder="例:\n10.1021/jacs.xxxxx\nhttps://doi.org/10.1039/xxxxx\nhttps://arxiv.org/abs/2301.12345",
                height=80,
                help="DOI、doi.orgのURL、出版社URL、arXiv URLに対応しています。",
            )
            max_candidates = st.slider("候補数上限", 50, 500, 200, 50)
            min_score_for_llm = st.slider(
                "LLM採点スキップ閾値（この点数未満はGemini採点しない）",
                min_value=0, max_value=80, value=50, step=5,
                help="事前スコア（TF-IDF + embedding + 新しさ + 被引用数など）がこの点数未満の論文はGeminiによる詳細評価をスキップします。処理時間の削減に効果的です。",
            )

        st.divider()
        use_abstract = st.toggle(
            "アブストラクトをスコアリングに使用する",
            value=True,
            help=(
                "ON（推奨）: タイトル＋アブストラクトでテーマ一致度を計算。"
                "アブストラクトが公開されているオープンアクセス論文が有利になります。\n\n"
                "OFF: タイトルのみで計算。有料論文（アブストラクト非公開）も同じ条件で評価されます。"
            ),
        )
        use_semantic_scholar = st.toggle(
            "Semantic Scholar も検索する",
            value=False,
            help=(
                "ON: OpenAlexに加えてSemantic Scholarからも候補を収集します。"
                "候補数が増え、有料論文もより多く含まれます。収集時間が長くなります（+30〜60秒）。\n\n"
                "OFF: OpenAlexのみ（デフォルト）。"
            ),
        )

        submitted = st.form_submit_button("検索開始", type="primary", use_container_width=True)

    if submitted:
        if not theme_text.strip():
            st.error("テーマ文は必須です。")
            return
        if not job_title.strip():
            st.error("検索タイトルは必須です。")
            return

        seed_dois = [_extract_doi(d.strip()) for d in seed_dois_text.splitlines() if d.strip()][:3]
        seed_dois = [d for d in seed_dois if d]  # 抽出失敗を除外

        # 日本語 → 英語翻訳
        theme_text_en = theme_text.strip()
        include_terms_en = include_terms.strip() or None

        if settings.deepl_api_key:
            with st.spinner("日本語を英語に翻訳中（DeepL）..."):
                theme_text_en = translate_to_english(theme_text.strip(), settings.deepl_api_key)
                if include_terms.strip():
                    include_terms_en = translate_terms(include_terms.strip(), settings.deepl_api_key)
        else:
            if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", theme_text):
                st.warning("DeepL APIキーが未設定のため翻訳できません。英語で入力するか、.envにDEEPL_API_KEYを設定してください。")

        # 翻訳結果を表示（元の入力と異なる場合）
        if theme_text_en != theme_text.strip():
            st.info(f"翻訳クエリ: **{theme_text_en}**")

        with st.spinner("候補論文を収集・採点中です...（数十秒かかる場合があります）"):
            try:
                with get_db() as db:
                    job = create_search_job(
                        db=db,
                        title=job_title.strip(),
                        theme_text=theme_text.strip(),   # 元の日本語をDBに保存
                        year_from=int(year_from),
                        year_to=int(year_to),
                        include_terms=include_terms.strip() or None,
                        exclude_terms=exclude_terms.strip() or None,
                        subject_tags=subject_tags.strip() or None,
                        seed_dois=seed_dois,
                        max_candidates=max_candidates,
                        use_abstract=use_abstract,
                    )
                    # OpenAlex検索・スコアリングには英語訳を使用
                    job.query.theme_text = theme_text_en
                    candidates = run_collection(
                        db, job,
                        use_semantic_scholar=use_semantic_scholar,
                        s2_api_key=settings.semantic_scholar_api_key or None,
                        gemini_api_key=settings.gemini_api_key or None,
                    )
                    st.info(f"{len(candidates)} 件の候補を収集しました。採点中（embedding類似度計算を含む）...")

                    score_all(
                        db=db,
                        job_id=job.job_id,
                        query_text=theme_text_en,
                        include_terms=include_terms_en,
                        use_abstract=use_abstract,
                    )

                    # Gemini APIキーがあれば上位候補をLLMで再採点
                    if settings.gemini_api_key:
                        progress_bar = st.progress(0, text="上位候補をGeminiで再採点中... (0/?)")
                        eta_text = st.empty()
                        start_time = time.time()

                        def on_llm_progress(current: int, total: int):
                            pct = current / total if total > 0 else 1.0
                            progress_bar.progress(
                                pct,
                                text=f"上位候補をGeminiで再採点中... ({current}/{total})",
                            )
                            elapsed = time.time() - start_time
                            if current > 0:
                                per_item = elapsed / current
                                remaining = per_item * (total - current)
                                if remaining > 1:
                                    eta_text.caption(f"残り約 {int(remaining)} 秒")
                                else:
                                    eta_text.empty()

                        n_llm = rescore_top_with_llm(
                            db=db,
                            job_id=job.job_id,
                            query_text=theme_text_en,
                            api_key=settings.gemini_api_key,
                            min_score_for_llm=float(min_score_for_llm),
                            progress_callback=on_llm_progress,
                        )
                        progress_bar.empty()
                        eta_text.empty()
                        st.info(f"LLM採点完了（{n_llm} 件）")

                    saved_job_id = job.job_id

                st.success(f"完了しました！候補数: {len(candidates)} 件")
                st.session_state["current_job_id"] = saved_job_id
                st.session_state["page"] = "候補一覧"
                st.rerun()

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
                raise


def _extract_doi(text: str) -> str | None:
    """
    テキストからDOIを抽出する。
    対応形式:
      - 生DOI:          10.1021/jacs.xxxxx
      - doi.org URL:    https://doi.org/10.1021/jacs.xxxxx
      - dx.doi.org URL: https://dx.doi.org/10.1021/jacs.xxxxx
      - 出版社URL:      https://pubs.acs.org/doi/10.1021/jacs.xxxxx
      - arXiv URL:      https://arxiv.org/abs/2301.12345  → arxiv:2301.12345
    """
    text = text.strip()
    if not text:
        return None

    # arXiv URL → arXiv IDに変換（OpenAlexはarXiv DOIに対応）
    arxiv_match = re.search(r"arxiv\.org/abs/([\d\.]+v?\d*)", text, re.IGNORECASE)
    if arxiv_match:
        return f"10.48550/arXiv.{arxiv_match.group(1)}"

    # DOIパターン（10.から始まる）を探す
    doi_match = re.search(r"(10\.\d{4,}/[^\s\"'<>]+)", text)
    if doi_match:
        doi = doi_match.group(1).rstrip(".,;)")  # 末尾の句読点を除去
        return doi

    return None
