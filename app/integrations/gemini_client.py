import re
import json
import time
from google import genai


def _has_japanese(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text))


def translate_to_english(text: str, api_key: str) -> str:
    """日本語テキストを英語に翻訳する。英語のみの場合はそのまま返す。"""
    if not text.strip():
        return text
    if not _has_japanese(text):
        return text
    if not api_key:
        return text

    client = genai.Client(api_key=api_key)
    prompt = (
        "Translate the following Japanese text to English for academic paper search. "
        "Output only the translated English text, no explanation.\n\n"
        f"{text}"
    )
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=prompt,
    )
    return response.text.strip()


def translate_terms(terms_str: str, api_key: str) -> str:
    """カンマ区切りキーワード列を英語に翻訳する。"""
    if not terms_str or not _has_japanese(terms_str) or not api_key:
        return terms_str

    client = genai.Client(api_key=api_key)
    prompt = (
        "Translate each of the following comma-separated Japanese keywords to English. "
        "Output only the comma-separated English keywords, no explanation.\n\n"
        f"{terms_str}"
    )
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=prompt,
    )
    return response.text.strip()


def expand_search_queries(theme_text: str, api_key: str, n_variants: int = 3) -> list[str]:
    """
    テーマ文から多様な英語検索クエリをn_variants個生成して返す。
    元クエリを含むリストを返す（失敗時は元クエリのみ）。
    """
    if not api_key or not theme_text.strip():
        return [theme_text]

    prompt = f"""You are an expert in academic literature search.
Generate {n_variants} diverse English search queries to broadly find papers related to the research theme below.
The theme may be written in Japanese or English — always output queries in English.

Research theme: {theme_text}

Rules:
- ALL queries must be in English (translate if the theme is in Japanese)
- Use different synonyms, related concepts, and terminology for each query
- Each query should be 4-8 words suitable for academic search APIs (OpenAlex, Semantic Scholar)
- Cover different angles: materials, methods, applications, characterization
- Output as JSON array only, no explanation: ["query1", "query2", "query3"]"""

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt,
        )
        raw = response.text.strip()
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if json_match:
            variants = json.loads(json_match.group())
            if isinstance(variants, list):
                queries = [theme_text] + [str(q) for q in variants[:n_variants] if q]
                return queries
    except Exception:
        pass
    return [theme_text]


def score_method_relevance(
    query_text: str,
    title: str,
    abstract: str | None,
    api_key: str,
    timeout_sec: int = 20,
) -> dict:
    """
    論文の手法一致度をLLMで判定する。

    Returns:
        {
            "method_relevance_score": float (0.0〜1.0),
            "summary": str (総評 2-3文),
            "matched_methods": str (一致した手法・材料のリスト),
            "concerns": str (懸念点・不一致点),
        }
    """
    if not api_key:
        return {"method_relevance_score": 0.0, "summary": "", "matched_methods": "", "concerns": ""}

    abstract_text = abstract or "(abstract not available)"
    prompt = f"""あなたは化学・材料科学の専門家です。
以下の論文が、ユーザーの研究テーマにどれだけ関連しているかを評価してください。

ユーザーの研究テーマ（英語）:
{query_text}

論文タイトル:
{title}

論文アブストラクト:
{abstract_text}

以下のJSON形式のみで回答してください（他のテキストは不要）:
{{
  "method_relevance_score": <0.0〜1.0の小数>,
  "summary": "<関連性の総評を1文（40字以内）の日本語で>",
  "matched_methods": "<一致する手法・材料をカンマ区切りで。なければ「なし」>",
  "concerns": "<主な懸念点を1文（40字以内）で。なければ「なし」>"
}}

ルール:
- method_relevance_score: 1.0=非常に関連あり、0.0=全く関連なし
- summary: テーマとの接点または乖離を端的に1文で
- matched_methods: 具体的な手法・材料名（例: 「ホットインジェクション合成、CsPbBr3」）
- concerns: スコープのズレや欠如点を端的に1文で
- JSONのみ出力"""

    client = genai.Client(api_key=api_key)
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite-preview",
                contents=prompt,
            )
            raw = response.text.strip()
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {
                    "method_relevance_score": float(
                        max(0.0, min(1.0, data.get("method_relevance_score", 0.0)))
                    ),
                    "summary": str(data.get("summary", ""))[:500],
                    "matched_methods": str(data.get("matched_methods", ""))[:300],
                    "concerns": str(data.get("concerns", ""))[:300],
                }
        except Exception:
            if attempt < 2:
                time.sleep(1)

    return {"method_relevance_score": 0.0, "summary": "", "matched_methods": "", "concerns": ""}
