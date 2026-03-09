# スコアリング実装方針メモ

## 1. 目的
本メモは、論文探索・順位付けアプリにおけるスコアリング機能の実装方針を、開発者がそのまま設計・実装に落とせる粒度で整理したものである。

対象は以下。
- 候補論文の自動順位付け
- スコア内訳の保存
- 処理速度と精度のバランスを取った実装
- LLMの使いどころの限定

---

## 2. 結論
推奨方式は **「ルールベース + 類似度 + 最小限のLLM」** の3段階ハイブリッドである。

理由は以下。
1. 全件にLLMを使うと遅く、高コストで、結果の揺れも出やすい
2. ルールベースとテキスト類似度で大半の順位は十分に決められる
3. LLMは上位候補の説明文生成や微調整に限定すると、精度向上の効果が高い
4. この方式は再現性、監査性、処理速度のバランスがよい

---

## 3. スコアリングの全体構成

### 3.1 3層構造
スコアリング処理は次の3層で構成する。

#### 第1層: ルールベース前処理
目的:
- 明らかに対象外の論文を早期に除外する
- 年や論文種別などの明確な条件を高速に処理する

対象例:
- 必須キーワードが1つも含まれない
- 除外キーワードを含む
- 年範囲外
- abstractなし
- review only / article only の条件不一致

#### 第2層: 類似度ベース本採点
目的:
- テーマに近い論文を定量的に順位付けする
- 明示的一致と意味的一致を両立する

対象手法:
- PostgreSQL全文検索またはTF-IDF
- embedding cosine similarity

#### 第3層: LLM後処理
目的:
- 上位候補だけを追加評価する
- 説明文を生成する
- 僅差候補の補正を行う

対象例:
- method paper かどうかの補助判定
- 手法一致度の微調整
- why_relevant の1行生成

---

## 4. 採点方針

### 4.1 基本配点
100点満点で次を採用する。
- テーマ一致度: 35点
- 手法一致度: 20点
- 新しさ: 15点
- 影響度: 15点
- 読みやすさ: 10点
- 役割補正: 5点

### 4.2 実装上の考え方
各項目は最終的に 0.0〜1.0 に正規化した値を作り、配点を掛けて点数化する。

例:
- テーマ一致度 = 35 × normalized_theme_score
- 手法一致度 = 20 × normalized_method_score

合計点は以下。

```text
total_score =
  theme_score +
  method_score +
  recency_score +
  impact_score +
  readability_score +
  role_bonus
```

---

## 5. 各スコアの具体実装

## 5.1 テーマ一致度（35点）

### 推奨内訳
- embedding類似度: 20点
- 全文検索/TF-IDF類似度: 10点
- 必須キーワード一致: 5点

### 実装意図
テーマ一致度は最重要項目であり、「語が一致しているか」と「意味が近いか」の両方を見る。

### 入力データ
- query_text: ユーザーが入力したテーマ文
- paper_text: title + abstract + keyword + journal を連結したテキスト

### 5.1.1 embedding類似度（20点）
query_text と paper_text を embedding 化し、cosine similarity を計算する。

```text
embed_norm = max(0, cosine_similarity(query_vec, paper_vec))
embed_score = 20 * embed_norm
```

補足:
- negative値が出る設計なら0に丸める
- 候補数が多い場合は embedding は title + abstract のみに限定してもよい

### 5.1.2 全文検索/TF-IDF類似度（10点）
PostgreSQL全文検索またはTF-IDFで語の一致度を取る。

```text
text_norm = normalize(fts_rank or tfidf_cosine)
text_score = 10 * text_norm
```

推奨:
- MVPではPostgreSQL全文検索を優先
- 精度改善フェーズでTF-IDFやBM25相当の補強を検討

### 5.1.3 必須キーワード一致（5点）
ユーザーが指定した include_terms や内部辞書語の一致率で加点する。

例:
```text
keyword_hit_ratio = matched_required_terms / total_required_terms
keyword_score = 5 * keyword_hit_ratio
```

### テーマ一致度の式
```text
theme_score = embed_score + text_score + keyword_score
```

---

## 5.2 手法一致度（20点）

### 推奨内訳
- ルールベース辞書一致: 10点
- LLM補助判定: 10点

### 実装意図
材料・化学系では、テーマが近くても手法が違うと実用性が大きく変わるため、別軸として評価する。

### 対象語の例
- 合成法: hot injection, LARP, microfluidic, flow synthesis, solvothermal など
- 評価法: PL, XRD, TEM, NMR, absorption など
- 材料系: perovskite, CsPbBr3, quantum dots, polymer composite など

### 5.2.1 ルールベース辞書一致（10点）
内部辞書を持ち、title/abstract 内に何個一致するかを数える。

```text
method_hit_ratio = weighted_matches / weighted_possible_matches
method_rule_score = 10 * clamp(method_hit_ratio, 0, 1)
```

推奨:
- 単純な語数一致ではなく重み付きにする
- 例えば合成法一致を重く、分析法一致を軽めにする

#### 例
- 合成法一致: weight 0.5
- 材料一致: weight 0.3
- 評価法一致: weight 0.2

### 5.2.2 LLM補助判定（10点）
LLMは上位候補だけに使う。

目的:
- 手法が本当に目的に直結するかを短文から補助判定する
- 語が一致しても文脈がズレている論文を下げる

推奨プロンプト例:
```text
入力:
- ユーザーの目的テーマ
- 論文タイトル
- 論文要旨

出力:
- method_relevance_score: 0.0〜1.0
- short_reason: 50字以内
```

```text
method_llm_score = 10 * method_relevance_score
method_score = method_rule_score + method_llm_score
```

注意:
- LLMは全件に掛けない
- 上位20〜50件のみ
- スコアだけでなく短い説明を返させる

---

## 5.3 新しさ（15点）

### 実装意図
新しい知見を優先したいが、古典的重要論文を完全に不利にしないようにする。

### 推奨実装
論文年から機械的に計算する。

例:
```text
paper_age = current_year - paper_year

if paper_age <= 2: recency_norm = 1.0
elif paper_age <= 5: recency_norm = 0.8
elif paper_age <= 8: recency_norm = 0.6
elif paper_age <= 12: recency_norm = 0.4
else: recency_norm = 0.2

recency_score = 15 * recency_norm
```

補足:
- review 論文は少し高め補正してもよい
- seminal 扱いは role_bonus 側で補正する

---

## 5.4 影響度（15点）

### 実装意図
被引用数や分野内での基盤性を反映する。

### 推奨実装
被引用数の絶対値ではなく、対数変換または分位点化を使う。

例:
```text
impact_norm = log(1 + citation_count) / log(1 + citation_count_p95)
impact_norm = clamp(impact_norm, 0, 1)
impact_score = 15 * impact_norm
```

代替案:
- 同一年内の citation percentile を使う
- review は一定加点

注意:
- 古い論文ほど引用が多くなりやすいので、年補正を入れるとよりよい

---

## 5.5 読みやすさ（10点）

### 実装意図
重要だが読みにくい論文もあるため、主評価にはしないが軽く反映する。

### ルールベース実装例
- abstractあり: +3
- titleが短すぎず意味が明確: +2
- review/method論文: +2
- metadataが十分: +1
- 図表や要旨の情報量が高そう: +2（LLMまたは簡易ヒューリスティック）

例:
```text
readability_score = 0
if has_abstract: readability_score += 3
if title_length_ok: readability_score += 2
if paper_type in ['review', 'method']: readability_score += 2
if metadata_complete: readability_score += 1
if readability_hint: readability_score += 2
```

上限は10点に丸める。

---

## 5.6 役割補正（5点）

### 実装意図
レビュー、方法論、古典的重要論文など、用途上の価値を少しだけ補正する。

### 実装例
- review: +2
- method/protocol: +2
- seminal: +3
- direct application example: +1

複数該当しても最大5点に丸める。

```text
role_bonus = min(5, review_bonus + method_bonus + seminal_bonus + application_bonus)
```

---

## 6. 実際の処理順

### 6.1 一次スコアリング
対象: 全候補論文

処理:
1. ルールベース足切り
2. 全文検索/TF-IDF スコア計算
3. embedding 類似度計算
4. 新しさ計算
5. 影響度計算
6. 読みやすさ簡易計算
7. 一次 total_score を作成
8. 上位N件を選ぶ

### 6.2 二次スコアリング
対象: 上位N件（推奨20〜50件）

処理:
1. LLMで手法一致度の補助判定
2. LLMで why_relevant を生成
3. 必要なら僅差候補の順位補正
4. 最終 total_score を作成

---

## 7. 推奨アルゴリズム構成

### 7.1 MVP段階
- ルールベース
- PostgreSQL全文検索
- 新しさ
- 影響度
- 読みやすさ簡易判定
- role_bonus

この段階では LLM なしでもよい。

### 7.2 精度改善段階
- embedding 類似度を追加
- 上位候補にだけ LLM を適用

### 7.3 本番推奨構成
- 全件: ルール + 全文検索 + embedding
- 上位候補: LLM補助
- 出力: total_score + 内訳 + 1行理由

---

## 8. 推奨データ項目

score_detail テーブルに以下を保存する。
- candidate_id
- theme_score
- method_score
- recency_score
- impact_score
- readability_score
- role_bonus
- total_score
- theme_embed_score
- theme_text_score
- theme_keyword_score
- method_rule_score
- method_llm_score
- scoring_version
- scored_at

補足:
- 再現性のため scoring_version は必須
- 将来の式変更に備えて内訳も保持する

---

## 9. 疑似コード

```python
def score_candidate(candidate, query, config):
    if not passes_filters(candidate, query, config):
        return zero_score_with_reject_reason()

    paper_text = build_paper_text(candidate)

    theme_embed = calc_embedding_similarity(query.text, paper_text)
    theme_text = calc_text_similarity(query.text, paper_text)
    theme_keyword = calc_keyword_match(query.include_terms, paper_text)
    theme_score = 20 * theme_embed + 10 * theme_text + 5 * theme_keyword

    method_rule = calc_method_rule_score(query.method_dict, paper_text)
    method_llm = None
    method_score = 10 * method_rule

    recency_score = calc_recency_score(candidate.year)
    impact_score = calc_impact_score(candidate.citation_count)
    readability_score = calc_readability_score(candidate)
    role_bonus = calc_role_bonus(candidate)

    total_score = (
        theme_score + method_score + recency_score +
        impact_score + readability_score + role_bonus
    )

    return {
        'theme_score': theme_score,
        'method_score': method_score,
        'recency_score': recency_score,
        'impact_score': impact_score,
        'readability_score': readability_score,
        'role_bonus': role_bonus,
        'total_score': total_score,
        'method_llm_score': method_llm,
    }
```

二次スコアリング:

```python
def rescore_top_candidates_with_llm(candidates, query):
    for c in candidates:
        llm_result = ask_llm_method_relevance(
            query_text=query.text,
            title=c.title,
            abstract=c.abstract,
        )
        c.method_llm_score = 10 * llm_result['method_relevance_score']
        c.method_score += c.method_llm_score
        c.total_score += c.method_llm_score
        c.reason_text = llm_result['short_reason']

    return rerank(candidates)
```

---

## 10. 技術選定の推奨

### 10.1 PostgreSQL側
- Full Text Search を使う
- 将来的に pgvector を導入する

### 10.2 Python側
- ルールベース: 素のPythonで十分
- TF-IDF: scikit-learn でも可
- embedding: 外部APIまたは社内標準モデル
- LLM: Claude 等を上位候補だけに利用

### 10.3 実装順
1. ルールベース + FTS
2. embedding追加
3. LLM追加

---

## 11. なぜこの設計が適切か

### 11.1 処理速度
- 全件をLLMに渡さないため速い
- 足切りと一次ランキングをDB中心で実行できる

### 11.2 精度
- キーワード一致だけでなく意味的近さも見られる
- 材料・化学分野で重要な「手法一致」を独立評価できる

### 11.3 再現性
- ルールと数式が固定される
- スコア内訳をDBに保存できる
- LLMを補助用途に限定するため揺れの影響を抑えられる

### 11.4 説明可能性
- なぜ高得点かを項目別に説明できる
- 開発者・利用者・管理者の誰にも説明しやすい

---

## 12. 開発者向け最終指示

最初の実装では、以下の順で作る。

### 必須実装
- ルールベース足切り
- PostgreSQL全文検索スコア
- 新しさスコア
- 影響度スコア
- 読みやすさスコア
- role_bonus
- total_score 計算
- score_detail 保存

### 次段階実装
- embedding 類似度
- 上位候補へのLLM適用
- reason_text の生成

### 実装ポリシー
- LLMは初期実装では必須にしない
- score_detail には内訳を必ず保存する
- scoring_version を導入し、式変更に備える
- 計算式は設定ファイル化して重みを変更できるようにする

---

## 13. 推奨初期設定値

```yaml
scoring:
  theme:
    embed_weight: 20
    text_weight: 10
    keyword_weight: 5
  method:
    rule_weight: 10
    llm_weight: 10
  recency_weight: 15
  impact_weight: 15
  readability_weight: 10
  role_bonus_weight: 5

llm:
  enabled_for_top_n: 30
  timeout_sec: 20
  max_reason_length: 50
```

---

## 14. 一言での実装方針
**全件はルールと類似度で高速採点し、LLMは上位候補の説明と微調整だけに使う。**
