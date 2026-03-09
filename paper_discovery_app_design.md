# 論文探索・順位付け・共有アプリ 設計書

## 1. 文書の目的
本書は、化学・材料分野の学術論文を効率的に収集し、読むべき論文に優先順位を付け、社内で共有できるアプリを開発するための実装設計書である。

この文書だけを見れば、開発担当者が以下を理解できることを目的とする。
- 何を作るのか
- なぜその構成にするのか
- どのような画面・DB・処理フローで実装するのか
- 最初にどこまで作ればよいのか

---

## 2. ゴール

### 2.1 解決したい課題
- 自分が知りたい内容の論文へ効率的にアクセスしたい
- 化学・材料分野で使いやすいことが重要
- 誰でもある程度同じ手順をたどれることが重要
- 論文リストを他者と共有できることが必要
- 優先順位に納得感が必要

### 2.2 最終成果物
アプリの出力成果物は次のとおり。
- テーマごとの論文候補一覧
- 各論文のスコアと順位
- なぜその順位なのかの説明文
- 社内共有用Excelファイル
- 必要に応じたCSVファイル

### 2.3 設計方針
設計方針は次の4点。
1. ユーザーの入力を最小化する
2. 候補収集は自動化する
3. 優先順位の根拠を見える化する
4. 共有方法はシンプルにする

---

## 3. 全体コンセプト

### 3.1 基本思想
アプリは、利用者から見ると1つのシンプルな論文探索ツールとして振る舞う。
内部では以下を順に行う。
1. 検索条件の入力
2. 候補論文の収集
3. 関連論文の拡張
4. 論文情報の正規化と重複除去
5. 自動採点と順位付け
6. 人による最終確認
7. DB保存
8. Excel/CSV出力

### 3.2 採用アーキテクチャ
- フロントエンド: Web UI
- バックエンド: Pythonアプリ
- データベース: PostgreSQL
- 配布先: 社内共有フォルダまたはOneDrive上のExcel/CSV
- 外部論文データソース: OpenAlex, Semantic Scholar

### 3.3 なぜこの構成か
- 利用者は複数サービスを意識しなくてよい
- データの正本をDBに置ける
- 共有はExcelに落とせるため受け手のITスキルに依存しにくい
- 検索条件・順位・理由を再現できる

---

## 4. 非機能要件

### 4.1 使いやすさ
- できるだけ少ない入力で検索開始できること
- 結果画面で「何が上位か」「なぜ上位か」がすぐ分かること
- 非技術者でもExcel成果物を見れば利用できること

### 4.2 再現性
- 同じ検索条件なら同じ候補集合に近い結果を再生成できること
- 収集経路と採点根拠を保存すること

### 4.3 保守性
- APIごとの処理をモジュール分割すること
- 画面とロジックを分離すること
- DBスキーマを業務単位で整理すること

### 4.4 共有性
- 共有フォルダやOneDriveにxlsx/csvを書き出せること
- DBに保存された結果をいつでも再出力できること

---

## 5. システム全体像

```text
[利用者]
  ↓
[Web UI]
  ↓
[アプリサーバー]
  - 検索条件受付
  - OpenAlex検索
  - Semantic Scholar推薦
  - 重複除去
  - スコア計算
  - 最終順位決定
  - Excel/CSV出力
  ↓
[PostgreSQL]
  - 検索条件
  - 論文マスタ
  - 候補集合
  - スコア内訳
  - 最終順位
  - コメント
  - 出力履歴
  ↓
[共有先]
  - 社内共有フォルダ
  - OneDrive
```

### 5.1 推奨配置
- PostgreSQLは社内サーバーまたは常時稼働PCに設置する
- Webアプリは社内LANからアクセス可能にする
- Excel出力先を共有フォルダまたはOneDriveに設定する

### 5.2 避けるべき構成
- DB本体ファイルを共有フォルダ同期で直接運用する構成
- ユーザーがExcelを正本として直接編集する構成
- 検索ロジックをExcelマクロへ過度に寄せる構成

---

## 6. 業務フロー

### 6.1 標準フロー
```text
[1. テーマ入力]
  ↓
[2. 候補収集]
  ↓
[3. 候補拡張]
  ↓
[4. 整形・重複除去]
  ↓
[5. 自動採点]
  ↓
[6. 上位論文の人手確認]
  ↓
[7. DB保存]
  ↓
[8. Excel/CSV出力]
```

### 6.2 詳細フロー
#### 1. テーマ入力
ユーザーは以下を入力する。
- 調べたいテーマ文
- 対象年の範囲
- seed論文 DOI/URL（任意、最大3本程度）
- 分野キーワード
- 除外語

#### 2. 候補収集
- OpenAlexでテーマ文とフィルタ条件に基づき候補を取得
- 初回母集団を作る

#### 3. 候補拡張
- OpenAlexの引用・被引用・関連論文から拡張
- Semantic Scholar recommendations から追加候補を取得

#### 4. 整形・重複除去
- DOIベースで統合
- DOIがない場合は title + year + first_author などで近似判定
- abstract, journal, year, authors を正規化

#### 5. 自動採点
各論文をスコアリングして暫定順位を作成する。

#### 6. 人手確認
- 上位10〜20件のみを見る
- 採用/保留/除外を付ける
- コメントを残す

#### 7. DB保存
- 検索条件
- 候補群
- スコア内訳
- 最終順位
- コメント
を保存する。

#### 8. Excel/CSV出力
- 上位論文一覧
- スコア内訳
- 理由付きリスト
を出力する。

---

## 7. 画面設計

画面は4つに限定する。

### 7.1 画面一覧
1. 検索作成画面
2. 候補一覧画面
3. 上位確認画面
4. 共有履歴画面

### 7.2 検索作成画面
#### 目的
新しい検索ジョブを作成する。

#### 入力項目
- 検索タイトル
- テーマ文
- 年範囲（from, to）
- 分野タグ
- seed論文 DOI/URL
- 除外語
- 候補数上限

#### ボタン
- 検索開始
- 下書き保存
- 条件クリア

#### 補足
- 高度な条件は折りたたみ表示にする
- 初期状態では最小項目だけ見せる

### 7.3 候補一覧画面
#### 目的
収集された候補を一覧で確認する。

#### 表示列
- Rank
- Title
- Year
- Journal
- DOI
- Discovery Path
- Theme Score
- Impact Score
- Total Score
- Why Relevant
- Status

#### フィルタ
- 上位のみ
- reviewのみ
- 未確認のみ
- 年範囲
- Discovery Path別

#### 操作
- 除外
- キープ
- 再採点
- 詳細表示

### 7.4 上位確認画面
#### 目的
上位論文のみ人手で最終判定する。

#### 表示内容
- タイトル
- abstract要約
- スコア内訳
- 推薦理由
- 収集経路

#### 入力項目
- 判定: 採用 / 保留 / 除外
- コメント
- タグ: review / seminal / method / useful など

#### 操作
- 前へ
- 次へ
- 一括確定
- Excel出力へ進む

### 7.5 共有履歴画面
#### 目的
過去検索結果を再出力・参照する。

#### 表示内容
- 検索タイトル
- 実行者
- 作成日時
- 最新出力日時
- 出力ファイルパス
- 形式（xlsx/xlsm/csv）

#### 操作
- Excel出力
- CSV出力
- 再出力
- 結果閲覧

---

## 8. DBテーブル設計

### 8.1 設計方針
- 検索ジョブ単位で履歴管理する
- 論文マスタはジョブ横断で再利用する
- 候補・スコア・順位はジョブ依存データとして分離する

### 8.2 テーブル一覧

#### app_user
利用者マスタ
- user_id (PK)
- name
- email
- department
- is_active
- created_at

#### search_job
1回の検索処理の単位
- job_id (PK)
- created_by (FK -> app_user.user_id)
- title
- status
- created_at
- completed_at
- note

#### search_query
検索条件
- query_id (PK)
- job_id (FK -> search_job.job_id)
- theme_text
- year_from
- year_to
- include_terms
- exclude_terms
- subject_tags
- max_candidates

#### seed_paper
起点論文
- seed_id (PK)
- job_id (FK -> search_job.job_id)
- doi
- url
- title
- note

#### paper
論文マスタ
- paper_id (PK)
- doi
- title
- abstract
- year
- journal
- authors_json
- source_primary
- openalex_id
- semantic_scholar_id
- created_at
- updated_at

#### paper_candidate
検索ジョブごとの候補
- candidate_id (PK)
- job_id (FK -> search_job.job_id)
- paper_id (FK -> paper.paper_id)
- discovery_path
- source_score_raw
- is_deduped
- is_shortlisted
- created_at

#### score_detail
スコア内訳
- score_id (PK)
- candidate_id (FK -> paper_candidate.candidate_id)
- theme_score
- method_score
- recency_score
- impact_score
- readability_score
- role_bonus
- total_score
- scoring_version
- scored_at

#### ranking_result
最終順位
- rank_id (PK)
- job_id (FK -> search_job.job_id)
- candidate_id (FK -> paper_candidate.candidate_id)
- final_rank
- decision
- reason_text
- finalized_at

#### review_note
手動確認メモ
- note_id (PK)
- rank_id (FK -> ranking_result.rank_id)
- reviewer_id (FK -> app_user.user_id)
- comment
- created_at

#### export_job
出力履歴
- export_id (PK)
- job_id (FK -> search_job.job_id)
- format
- output_path
- executed_at
- executed_by (FK -> app_user.user_id)

#### api_log
API実行記録
- log_id (PK)
- job_id (FK -> search_job.job_id)
- provider
- endpoint
- request_summary
- status_code
- requested_at

### 8.3 主なER関係
```text
app_user 1 --- n search_job
search_job 1 --- 1 search_query
search_job 1 --- n seed_paper
search_job 1 --- n paper_candidate
paper 1 --- n paper_candidate
paper_candidate 1 --- 1 score_detail
search_job 1 --- n ranking_result
paper_candidate 1 --- n ranking_result
ranking_result 1 --- n review_note
search_job 1 --- n export_job
search_job 1 --- n api_log
```

---

## 9. スコアリング設計

### 9.1 目的
論文の優先順位に納得感を持たせるため、ブラックボックスではなく説明可能な採点を行う。

### 9.2 推奨配点
100点満点。
- テーマ一致度: 35
- 手法一致度: 20
- 新しさ: 15
- 影響度: 15
- 読みやすさ: 10
- 役割補正: 5

### 9.3 各項目の意味
- テーマ一致度: 目的テーマにどれだけ近いか
- 手法一致度: 合成法、解析法、材料系が近いか
- 新しさ: 新しい重要論文か
- 影響度: 分野内でよく参照されるか
- 読みやすさ: abstractや誌面情報から見た読みやすさ
- 役割補正: review, seminal, protocol 等の加点

### 9.4 推奨出力
各候補に対し以下を出す。
- total_score
- スコア内訳
- why_relevant（1行）
- discovery_path

---

## 10. Excel成果物設計

### 10.1 出力ファイル
- ranked_papers.xlsx
- ranked_papers.csv
- 必要なら ranked_papers.xlsm

### 10.2 推奨シート構成
#### Sheet 1: Summary
- 検索タイトル
- テーマ文
- 作成日
- 実行者
- 件数
- 上位5件

#### Sheet 2: Ranked Papers
- Rank
- Decision
- Title
- Year
- Journal
- DOI
- Total Score
- Why Relevant
- Discovery Path
- Comment

#### Sheet 3: Score Breakdown
- Title
- Theme Score
- Method Score
- Recency Score
- Impact Score
- Readability Score
- Role Bonus
- Total Score

#### Sheet 4: Search Condition
- Theme Text
- Year From/To
- Include Terms
- Exclude Terms
- Seed Papers

### 10.3 マクロ利用の位置づけ
マクロは補助的に使う。
用途は次に限定する。
- 色付け
- ソート支援
- フィルタ支援
- 体裁調整

検索処理やスコアリングの本体はExcelマクロではなくアプリ側で持つ。

---

## 11. API連携方針

### 11.1 OpenAlex
役割:
- 検索条件に基づく候補収集
- 初回母集団の形成
- 引用・被引用・関連論文探索

### 11.2 Semantic Scholar
役割:
- 推薦候補の拡張
- 関連性の高い論文の追加探索

### 11.3 API連携時の注意
- 取得結果はそのまま使わず正規化する
- DOIを優先キーにする
- rate limitや一時失敗を考慮して再試行処理を入れる
- APIログを保存する

---

## 12. バックエンド処理設計

### 12.1 推奨モジュール構成
```text
app/
  ui/
  services/
    search_service.py
    expand_service.py
    dedupe_service.py
    scoring_service.py
    export_service.py
  integrations/
    openalex_client.py
    semanticscholar_client.py
  repositories/
    job_repository.py
    paper_repository.py
    ranking_repository.py
  models/
  tasks/
```

### 12.2 処理順
1. search_job 作成
2. search_query 保存
3. seed_paper 保存
4. OpenAlex検索
5. 候補保存
6. 関連論文拡張
7. paper正規化
8. 重複除去
9. score_detail 作成
10. ranking_result 作成
11. Excel/CSV出力
12. export_job 保存

### 12.3 非同期化の対象
非同期ジョブにしたい処理は次。
- API検索
- 候補拡張
- 大量候補の重複除去
- Excel生成

---

## 13. 開発フェーズ

### Phase 1: MVP
目的は「最小構成で価値を出す」こと。

実装範囲:
- 検索作成画面
- OpenAlex検索
- 候補一覧画面
- PostgreSQL保存
- xlsx/csv出力

MVPの完了条件:
- テーマ文を入れると候補一覧が作れる
- 候補に仮スコアが付く
- Excel出力できる

### Phase 2: 拡張
- Semantic Scholar推薦追加
- 上位確認画面追加
- コメント保存
- 出力履歴管理

### Phase 3: 運用強化
- スコアリング改善
- ユーザー権限管理
- 定期実行
- メール通知や社内通知

---

## 14. 開発時の具体タスク

### 14.1 最初にやること
- PostgreSQL環境を用意
- アプリ雛形作成
- DB migration導入
- OpenAlex接続実装
- 検索条件画面作成

### 14.2 次にやること
- candidate保存処理
- スコアリング処理
- 候補一覧画面
- Excel出力処理

### 14.3 その後やること
- Semantic Scholar連携
- 手動確認画面
- 再出力画面
- 運用ログ整備

---

## 15. 運用ルール

### 15.1 データ正本
- 正本はPostgreSQL
- Excelは配布物
- Excelの直接編集内容は正本へ戻さない

### 15.2 権限
- 検索実行者
- レビュー担当者
- 管理者

### 15.3 命名規則例
- Job Title: `YYYYMMDD_テーマ短縮名_作成者`
- 出力ファイル: `ranked_papers_YYYYMMDD_HHMM.xlsx`

---

## 16. リスクと対策

### 16.1 API由来の不安定さ
対策:
- リトライ
- エラーログ保存
- API障害時は途中保存

### 16.2 重複判定の揺れ
対策:
- DOI優先
- DOIなしは title/year/author で補助判定
- 手動統合フラグを後で追加可能にする

### 16.3 スコアの納得感不足
対策:
- スコア内訳を見せる
- 1行理由を出す
- 上位だけ人が確認する

### 16.4 Excel依存の肥大化
対策:
- ロジックはアプリ側に集中
- Excelは配布・閲覧用に限定

---

## 17. 今回の推奨実装結論

最初に作るべきものは以下。
1. PostgreSQLを正本にしたWebアプリ
2. OpenAlexベースの候補収集機能
3. スコア付き候補一覧画面
4. Excel/CSV自動出力

その後に追加するものは以下。
1. Semantic Scholarによる推薦拡張
2. 上位論文の手動確認UI
3. 出力履歴と再出力機能

---

## 18. 開発開始時の最小TODO
- [ ] PostgreSQLを社内サーバーに構築
- [ ] Pythonアプリのプロジェクト雛形を作成
- [ ] search_job/search_query/paper/paper_candidate テーブル作成
- [ ] OpenAlex検索APIクライアント実装
- [ ] 検索作成画面実装
- [ ] 候補一覧画面実装
- [ ] スコアリング仮ロジック実装
- [ ] xlsx/csv出力実装

---

## 19. 補足メモ
- DB本体をOneDrive同期対象にしない
- OneDriveは成果物ファイルの共有先として使う
- Excelマクロは補助的に使う
- まずはMVPを短期間で作り、その後スコア改善と共有改善に進む
