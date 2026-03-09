import streamlit as st
import pandas as pd
from ..database import get_db
from ..models.tables import SearchJob, ExportJob
from ..services.export_service import export_to_xlsx, export_to_csv, build_ranked_df
from ..config import to_jst_str


def _load_history(db) -> tuple[list[dict], list[dict]]:
    """ジョブ一覧と選択用リストをdict化して返す。"""
    jobs = db.query(SearchJob).order_by(SearchJob.created_at.desc()).all()
    rows = []
    job_list = []
    for j in jobs:
        exports = (
            db.query(ExportJob)
            .filter(ExportJob.job_id == j.job_id)
            .order_by(ExportJob.executed_at.desc())
            .all()
        )
        last_export = to_jst_str(exports[0].executed_at) if exports else "-"
        df = build_ranked_df(db, j.job_id)
        rows.append({
            "Job ID": j.job_id,
            "タイトル": j.title,
            "ステータス": j.status,
            "候補数": len(df),
            "作成日時": to_jst_str(j.created_at),
            "最終出力": last_export,
        })
        job_list.append({"label": f"[{j.job_id}] {j.title}", "job_id": j.job_id})
    return rows, job_list


def render():
    st.title("共有履歴・再出力")

    with get_db() as db:
        rows, job_list = _load_history(db)

    if not job_list:
        st.info("検索ジョブがありません。")
        return

    history_df = pd.DataFrame(rows)
    st.dataframe(history_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("再出力")

    job_options = {j["label"]: j["job_id"] for j in job_list}
    selected = st.selectbox("ジョブを選択", list(job_options.keys()))
    selected_job_id = job_options[selected]

    col1, col2 = st.columns(2)
    with col1:
        with get_db() as db:
            job_obj = db.query(SearchJob).filter(SearchJob.job_id == selected_job_id).first()
            xlsx_bytes = export_to_xlsx(db, job_obj)
        st.download_button(
            "Excel 再出力",
            data=xlsx_bytes,
            file_name=f"ranked_papers_{selected_job_id}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with col2:
        with get_db() as db:
            csv_bytes = export_to_csv(db, selected_job_id)
        st.download_button(
            "CSV 再出力",
            data=csv_bytes,
            file_name=f"ranked_papers_{selected_job_id}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    if st.button("結果を候補一覧で開く"):
        st.session_state["current_job_id"] = selected_job_id
        st.session_state["page"] = "候補一覧"
        st.rerun()
