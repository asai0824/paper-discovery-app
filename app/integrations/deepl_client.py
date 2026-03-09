import re
import requests


def _has_japanese(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text))


def _get_endpoint(api_key: str) -> str:
    # 無料版キーは末尾が :fx
    if api_key.endswith(":fx"):
        return "https://api-free.deepl.com/v2/translate"
    return "https://api.deepl.com/v2/translate"


def translate_to_english(text: str, api_key: str) -> str:
    """日本語テキストを英語に翻訳する。日本語が含まれない場合はそのまま返す。"""
    if not text.strip() or not api_key:
        return text
    if not _has_japanese(text):
        return text

    url = _get_endpoint(api_key)
    resp = requests.post(
        url,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        json={
            "text": [text],
            "source_lang": "JA",
            "target_lang": "EN-US",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["translations"][0]["text"].strip()


def translate_to_japanese(text: str, api_key: str) -> str:
    """英語テキストを日本語に翻訳する。日本語が既に含まれる場合はそのまま返す。"""
    if not text.strip() or not api_key:
        return text
    if _has_japanese(text):
        return text

    url = _get_endpoint(api_key)
    resp = requests.post(
        url,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        json={
            "text": [text],
            "source_lang": "EN",
            "target_lang": "JA",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["translations"][0]["text"].strip()


def translate_terms(terms_str: str, api_key: str) -> str:
    """カンマ区切りキーワード列を英語に翻訳する。"""
    if not terms_str or not api_key or not _has_japanese(terms_str):
        return terms_str

    # 各キーワードを個別に翻訳してカンマで結合
    terms = [t.strip() for t in terms_str.split(",") if t.strip()]
    if not terms:
        return terms_str

    url = _get_endpoint(api_key)
    resp = requests.post(
        url,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        json={
            "text": terms,
            "source_lang": "JA",
            "target_lang": "EN-US",
        },
        timeout=15,
    )
    resp.raise_for_status()
    translated = [t["text"].strip() for t in resp.json()["translations"]]
    return ", ".join(translated)
