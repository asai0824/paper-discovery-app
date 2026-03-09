import os
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from pydantic_settings import BaseSettings

_JST = timezone(timedelta(hours=9))


def to_jst_str(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """UTC datetimeをJST文字列に変換する。Noneの場合は空文字を返す。"""
    if dt is None:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone(_JST).strftime(fmt)


class Settings(BaseSettings):
    database_url: str = "sqlite:///./paper_discovery.db"
    gemini_api_key: str = ""
    deepl_api_key: str = ""
    semantic_scholar_api_key: str = ""
    app_host: str = "0.0.0.0"
    app_port: int = 8501

    class Config:
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"


def load_scoring_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "scoring.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_settings() -> Settings:
    """st.secrets（Streamlit Cloud）があればそちらを優先し、なければ.envを使う。"""
    try:
        import streamlit as st
        secrets = st.secrets
        overrides = {}
        key_map = {
            "database_url": ["DATABASE_URL", "database_url"],
            "gemini_api_key": ["GEMINI_API_KEY", "gemini_api_key"],
            "deepl_api_key": ["DEEPL_API_KEY", "deepl_api_key"],
            "semantic_scholar_api_key": ["SEMANTIC_SCHOLAR_API_KEY", "semantic_scholar_api_key"],
        }
        for field, candidates in key_map.items():
            for key in candidates:
                if key in secrets:
                    overrides[field] = secrets[key]
                    break
        if overrides:
            return Settings(**overrides)
    except Exception:
        pass
    return Settings()


settings = _load_settings()
scoring_config = load_scoring_config()
