import time
import streamlit as st
import pandas as pd
from ..database import get_db
from ..models.tables import SearchJob, RankingResult
from ..services.export_service import export_to_xlsx, export_to_csv, build_ranked_df
from ..services.scoring_service import rescore_top_with_llm
from ..config import settings


def render():
    st.title("候補一覧")

    # ---- ジョブ選択 ----
    with get_db() as db:
        jobs = db.query(SearchJob).order_by(SearchJob.created_at.desc()).all()
        job_options = {f"[{j.job_id}] {j.title} ({j.status})": j.job_id for j in jobs}

    if not job_options:
        st.info("検索ジョブがありません。まず「新規検索」から検索を作成してください。")
        return

    # session_stateに current_job_id があれば優先
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
    job_id = job_options[selected_label]

    with get_db() as db:
        job = db.query(SearchJob).filter(SearchJob.job_id == job_id).first()
        if not job:
            st.error("ジョブが見つかりません。")
            return

        df = build_ranked_df(db, job_id)

    if df.empty:
        st.warning("候補論文がありません。")
        return

    # ---- フィルタ ----
    st.subheader("フィルタ")
    col1, col2, col3 = st.columns(3)
    with col1:
        show_top_n = st.number_input("上位N件のみ表示（0=全件）", min_value=0, max_value=len(df), value=0, step=10)
    with col2:
        filter_decision = st.multiselect(
            "判定フィルタ",
            ["pending", "accepted", "hold", "rejected"],
            default=[],
        )
    with col3:
        year_min = int(df["Year"].dropna().min()) if not df["Year"].dropna().empty else 2000
        year_max = int(df["Year"].dropna().max()) if not df["Year"].dropna().empty else 2026
        year_range = st.slider("年範囲", year_min, year_max, (year_min, year_max))

    filtered_df = df.copy()
    if show_top_n > 0:
        filtered_df = filtered_df[filtered_df["Rank"] <= show_top_n]
    if filter_decision:
        filtered_df = filtered_df[filtered_df["Decision"].isin(filter_decision)]
    filtered_df = filtered_df[
        filtered_df["Year"].isna() |
        ((filtered_df["Year"] >= year_range[0]) & (filtered_df["Year"] <= year_range[1]))
    ]

    # ---- 判定変更 ----
    st.subheader(f"候補一覧（{len(filtered_df)} 件）")

    display_cols = ["Rank", "Decision", "Title", "Year", "Journal", "DOI Link",
                    "Total Score", "Theme Score", "Method Score",
                    "Recency Score", "Impact Score", "Discovery Path"]
    show_df = filtered_df[display_cols].copy()

    # スコア列を小数点1桁に
    for col in ["Total Score", "Theme Score", "Method Score", "Recency Score", "Impact Score"]:
        show_df[col] = show_df[col].round(1)

    st.dataframe(
        show_df,
        use_container_width=True,
        height=500,
        column_config={
            "DOI Link": st.column_config.LinkColumn("DOI", display_text="開く"),
            "Total Score": st.column_config.ProgressColumn(
                "Total Score", min_value=0, max_value=100, format="%.1f"
            ),
        },
        hide_index=True,
    )

    # ---- 判定変更フォーム ----
    with st.expander("判定を変更する"):
        ranks = filtered_df["Rank"].tolist()
        selected_rank = st.selectbox("Rank を選択", ranks)
        new_decision = st.radio(
            "判定",
            ["accepted", "hold", "rejected", "pending"],
            horizontal=True,
        )
        comment = st.text_input("コメント（任意）")

        if st.button("判定を保存"):
            row = filtered_df[filtered_df["Rank"] == selected_rank].iloc[0]
            with get_db() as db:
                ranking = (
                    db.query(RankingResult)
                    .filter(
                        RankingResult.job_id == job_id,
                        RankingResult.final_rank == selected_rank,
                    )
                    .first()
                )
                if ranking:
                    ranking.decision = new_decision
                    if comment:
                        ranking.reason_text = comment
            st.success(f"Rank {selected_rank} の判定を「{new_decision}」に更新しました。")
            st.rerun()

    # ---- LLM再採点 ----
    if settings.gemini_api_key:
        with st.expander("Gemini再採点（閾値調整）"):
            st.caption("初回採点でスキップされた論文に、閾値を下げてGeminiコメントを追加できます。")

            with get_db() as db:
                job_obj = db.query(SearchJob).filter(SearchJob.job_id == job_id).first()
                theme_text_for_llm = job_obj.query.theme_text if job_obj and job_obj.query else ""

            col_r1, col_r2 = st.columns([2, 1])
            with col_r1:
                rescore_threshold = st.slider(
                    "LLM採点スキップ閾値", min_value=0, max_value=80, value=50, step=5,
                    help="この点数以上でまだGemini採点されていない論文にコメントを追加します。",
                    key="rescore_threshold",
                )
            with col_r2:
                # 対象件数プレビュー
                unscored_count = df[
                    (df["Total Score"] >= rescore_threshold) &
                    (df.get("Method LLM Score", pd.Series(0, index=df.index)).fillna(0) == 0)
                ].shape[0] if "Method LLM Score" in df.columns else df[df["Total Score"] >= rescore_threshold].shape[0]
                st.metric("採点対象（未採点）", f"{unscored_count} 件")

            if st.button("Gemini再採点を実行", type="primary", key="rescore_btn"):
                progress_bar = st.progress(0, text="再採点中... (0/?)")
                eta_text = st.empty()
                start_time = time.time()

                def on_rescore_progress(current: int, total: int):
                    pct = current / total if total > 0 else 1.0
                    progress_bar.progress(pct, text=f"再採点中... ({current}/{total})")
                    elapsed = time.time() - start_time
                    if current > 0:
                        remaining = (elapsed / current) * (total - current)
                        if remaining > 1:
                            eta_text.caption(f"残り約 {int(remaining)} 秒")
                        else:
                            eta_text.empty()

                with get_db() as db:
                    n = rescore_top_with_llm(
                        db=db,
                        job_id=job_id,
                        query_text=theme_text_for_llm,
                        api_key=settings.gemini_api_key,
                        top_n=len(df),
                        min_score_for_llm=float(rescore_threshold),
                        only_unscored=True,
                        progress_callback=on_rescore_progress,
                    )
                progress_bar.empty()
                eta_text.empty()
                st.success(f"{n} 件にGeminiコメントを追加しました。")
                st.rerun()

    # ---- エクスポート ----
    st.subheader("エクスポート")
    col_xlsx, col_csv = st.columns(2)

    with col_xlsx:
        with get_db() as db:
            job_obj = db.query(SearchJob).filter(SearchJob.job_id == job_id).first()
            xlsx_bytes = export_to_xlsx(db, job_obj)
        st.download_button(
            label="Excel (.xlsx) ダウンロード",
            data=xlsx_bytes,
            file_name=f"ranked_papers_{job_id}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with col_csv:
        with get_db() as db:
            csv_bytes = export_to_csv(db, job_id)
        st.download_button(
            label="CSV ダウンロード",
            data=csv_bytes,
            file_name=f"ranked_papers_{job_id}.csv",
            mime="text/csv",
            use_container_width=True,
        )
