import streamlit as st

st.set_page_config(
    page_title="論文探索・順位付けアプリ",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _check_password() -> bool:
    """パスワード認証。st.secretsに"password"キーがなければスキップ。"""
    correct_password = st.secrets.get("password", None)
    if correct_password is None:
        return True  # secrets未設定時はローカル開発用としてスキップ

    if st.session_state.get("authenticated"):
        return True

    with st.container():
        st.title("📄 論文探索アプリ")
        pw = st.text_input("パスワード", type="password", key="_pw_input")
        if st.button("ログイン", type="primary"):
            if pw == correct_password:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("パスワードが違います。")
    return False


if not _check_password():
    st.stop()

from app.ui import search_page, candidate_page, review_page, history_page, doi_list_page


@st.cache_resource(show_spinner="embeddingモデルを読み込み中...")
def _preload_embed_model():
    """アプリ起動時にembeddingモデルをロードしてキャッシュする（初回のみ実行）。"""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        # scoring_service のキャッシュにも登録する
        from app.services.scoring_service import _embed_model_cache
        _embed_model_cache["model"] = model
        return model
    except Exception:
        return None


_preload_embed_model()

PAGES = {
    "新規検索": search_page,
    "マイリスト採点": doi_list_page,
    "候補一覧": candidate_page,
    "上位確認": review_page,
    "共有履歴": history_page,
}

with st.sidebar:
    st.title("📄 論文探索アプリ")
    st.divider()
    page = st.radio(
        "メニュー",
        list(PAGES.keys()),
        index=list(PAGES.keys()).index(st.session_state.get("page", "新規検索")),
    )
    st.session_state["page"] = page
    st.divider()
    st.caption("Phase 2")

PAGES[page].render()
