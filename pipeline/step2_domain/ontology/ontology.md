---
title: 障害者福祉ドメイン オントロジー（Phase3/4 成果物）
domain: bunkyo-disability-welfare
source_layer: /staging/llm-wiki/（LLM-Wiki）
status: draft — 値制約は人間レビュー必須（下記 §制約 参照）
updated: 2026-07-13
---

# 障害者福祉ドメイン オントロジー

`ontology_graphrag_pipeline_spec.md` の Phase3（オントロジー自動生成）／Phase4（プロパティグラフ化）の成果物。
**クラス・関係の骨格はLLM自動生成、値を伴う制約（金額・年齢・法的閾値）は人間レビュー必須**（§0の設計思想）。
出典はすべて LLM-Wiki（`pages/`, `entities/`）＝ステージング層。本オントロジーは `/ontology/domain/` 相当。

## クラス（ノードラベル）

| クラス | 説明 | 主なプロパティ | 由来 |
|---|---|---|---|
| `Service` | 制度・サービス（手当/医療費助成/給付/割引 等） | name, benefit_tiers, income_limit(bool), free_hours_per_month, age_range, excludes[], medical_care_child(bool), src | pages/01-22 |
| `ServiceCategory` | 章カテゴリ（手当年金/医療/福祉用具/交通…） | name | index.md |
| `TargetCategory` | 対象者区分（身/知/精/難/児） | code, name | [[target-categories]] |
| `Notebook` | 手帳・受給者証 | name, grade_type(級/度), grades[] | [[disability-notebooks]] |
| `Contact` | 窓口（部署・係） | dept, phone, fax, location | [[key-contacts]] |
| `Reference` | 参考資料（等級表・疾病一覧・所得制限） | name, kind, booklet_page | pages/99 |
| `Facility` | 施設・事業所 | name, phone, address | pages/22 |

- `Service` はカテゴリにより下位ラベルを含意（GRAPH TYPEの包含関係）: `:Allowance`（手当年金）, `:MedicalAid`（医療）, `:AssistiveDevice`（福祉用具）, `:TransportBenefit`（交通/割引）は全て `:Service`。

## 関係（エッジ型）

| 関係 | from → to | プロパティ | 意味 |
|---|---|---|---|
| `HAS_CATEGORY` | Service → ServiceCategory | — | 制度の分類 |
| `TARGETS` | Service → TargetCategory | — | 対象者区分（身/知/精/難/児） |
| `REQUIRES` | Service → Notebook | grades[] | 対象となる手帳と等級 |
| `PROVIDES_BENEFIT` | Service →（プロパティ benefit_tiers に内包） | criteria, amount, freq | 給付内容・金額 |
| `ADMINISTERED_BY` | Service → Contact | for（対象者別窓口）, hours | 申請・問い合わせ窓口 |
| `DEFINED_BY` | Service → Reference | — | 対象疾病/所得制限等の定義参照 |
| `MUTUALLY_EXCLUSIVE_WITH` | Service ↔ Service | basis | 併給不可（受給できない方の条項） |
| `RELATED_TO` | Service ↔ Service | — | 関連制度（wikiの[[リンク]]由来） |
| `PROVIDED_AT` | Facility → Service | — | 施設が提供するサービス |

## GRAPH TYPE スキーマ（Neo4j 2026.02 Preview / 疑似）

> ⚠ Preview機能。構文は変わり得る。値レンジ制約（`amount>=0`等）はGRAPH TYPEでカバーされない可能性があるため、
> アプリ層(FastAPI)または既存プロパティ制約で補完する（spec §0 注意点）。**オープングラフ型**：Domain/Task層のみ厳格、Instance層は柔軟。

```cypher
// --- Node types (安定部分=厳格) ---
CREATE GRAPH TYPE BunkyoWelfare {
  ServiceCategory ({ name :: STRING NOT NULL }),
  TargetCategory  ({ code :: STRING NOT NULL, name :: STRING }),
  Notebook        ({ name :: STRING NOT NULL, grade_type :: STRING }),
  Contact         ({ dept :: STRING NOT NULL, phone :: STRING, fax :: STRING, location :: STRING }),
  Reference       ({ name :: STRING NOT NULL, kind :: STRING, booklet_page :: STRING }),
  Service         ({ name :: STRING NOT NULL,
                     income_limit :: BOOLEAN,
                     free_hours_per_month :: INTEGER,
                     medical_care_child :: BOOLEAN }),

  // --- Relationship types with endpoint constraints ---
  (:Service)-[:HAS_CATEGORY]->(:ServiceCategory),
  (:Service)-[:TARGETS]->(:TargetCategory),
  (:Service)-[:REQUIRES { grades :: LIST<STRING> }]->(:Notebook),
  (:Service)-[:ADMINISTERED_BY { for :: STRING, hours :: STRING }]->(:Contact),
  (:Service)-[:DEFINED_BY]->(:Reference),
  (:Service)-[:MUTUALLY_EXCLUSIVE_WITH { basis :: STRING }]->(:Service),
  (:Service)-[:RELATED_TO]->(:Service),
  (:Facility)-[:PROVIDED_AT]->(:Service)
}
```

## 制約（constraints）と自然言語逆翻訳（Phase3-3）

各制約を平易な日本語に逆翻訳（ドメインエキスパートレビュー用）。**`review: human_required` は金額・年齢・法的閾値のため自動採用禁止**（出典＝原本ページを必ず確認）。

| ID | 制約（形式） | 自然言語逆翻訳 | 出典 | review |
|---|---|---|---|---|
| C01 | `心身障害者等福祉手当.benefit(身体1・2級 ∨ 愛1-3度 ∨ 脳性麻痺 ∨ 進行性筋萎縮症) = 15500` | 身体手帳1・2級／愛の手帳1〜3度／脳性麻痺／進行性筋萎縮症の方の月額は15,500円 | 04 (原本44p) | **human_required** |
| C02 | `心身障害者等福祉手当.benefit(身体3級 ∨ 愛4度) = 13500` | 身体手帳3級／愛の手帳4度の方の月額は13,500円 | 04 (44p) | **human_required** |
| C03 | `精神障害者福祉手当.requires = 精神手帳1級 ∧ benefit = 10000` | 精神障害者福祉手当は精神手帳1級が対象で月額10,000円 | 04 (45p) | **human_required** |
| C04 | `心身障害者等福祉手当.excludes ⊇ {取得年齢≥65, 児童育成手当(障害手当)受給者, 施設入所者, 所得超過}` | 手帳取得が65歳以上、児童育成手当(障害手当)受給者、施設入所者、所得超過の人は受給不可 | 04 (44p) | human_required（年齢閾値） |
| C05 | `マル障.requires = {身体1・2級 ∨ 身体3級(内部) ∨ 愛1・2度 ∨ 精神1級}` | マル障の対象は身体1・2級（内部障害は3級含む）／愛の手帳1・2度／精神1級 | 05 (56p) | human_required（等級） |
| C06 | `移動支援.free_hours_per_month = 36`（同行援護も同じ） | 同行援護・移動支援は月36時間まで利用者負担なし | 03/07 (43/82p) | **human_required** |
| C07 | `補装具.DEFINED_BY = 難病対象疾病一覧(376疾病, 原本199p)` | 難病の補装具対象疾病は「障害者総合支援法の難病患者等対象疾病一覧」で確認 | 06/99 (199p) | none（参照のみ） |
| C08 | `∀ Allowance with income_limit=true : DEFINED_BY 所得制限限度額表(原本204-205p)` | 所得制限のある手当の限度額は所得制限限度額基準表で確認 | 99 (204p) | none |
| C09 | `MUTUALLY_EXCLUSIVE_WITH(精神福祉手当, 心身障害者等福祉手当)` | 精神障害者福祉手当と心身障害者等福祉手当は併給不可 | 04 (45p) | none |
| C10 | `医療的ケア児在宅レスパイト.age_range = '〜18歳(年度末)' ∧ 上限288h/年` | 在宅レスパイトは18歳(年度末)まで、年288時間上限 | 07 (85p) | human_required（時間上限） |

### 整合性チェック（Phase3-2 相当）
- OWLリーズナー（HermiT等）の代替として、`graph/validate_kg.py` が
  (a) 参照整合性（全エッジの端点ノードが存在）、(b) 制約 C01–C10 の充足、(c) CQ回答テスト を実行する。
- `disjointWith`相当: `TargetCategory` の5コードは排他的だが1サービスは複数を`TARGETS`し得る（重複可）。矛盾なし。

## ガバナンス対応（spec §三層ガバナンス）
- **語彙・構造**（このファイルのクラス/関係/GRAPH TYPE）→ Git/CI/CD・PRレビュー。
- **業務ルール=制約値**（C01–C10のうち human_required）→ フォームUI＋承認フロー。金額・閾値・等級は原本で照合してから確定。
- **参照データ**（`kg.json` のInstance層）→ 直接CRUD＋`validate_kg.py`検証。

## 未確定・レビュー待ち
- benefit金額（C01-C03）・時間上限（C06,C10）・対象等級（C04,C05）は**原本＝令和7年5月末時点**の値。改定され得るため、本番投入前にドメインエキスパートが一次情報で確認・承認する（spec Phase3-5 レビューUI）。
