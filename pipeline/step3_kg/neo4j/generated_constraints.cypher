// ── オントロジー定義から自動生成された制約 ──
// 生成日時: 2026-07-15T20:49:53.528496
// このファイルは ontology 再生成時に上書きされます。

// C1 [PhysicalDisabilityHandbook.gradeOrDegree]: 身体障害者手帳の等級は1級から6級までです。
//   値: 1〜6級None
// 出典: disability-notebooks

// C2 [AiNoTecho.gradeOrDegree]: 愛の手帳の度数は1度から4度までです。
//   値: 1〜4度None
// 出典: disability-notebooks

// C3 [MentalDisabilityHandbook.gradeOrDegree]: 精神障害者保健福祉手帳の等級は1級から3級までです。
//   値: 1〜3級None
// 出典: disability-notebooks

// C4 [AiNoTecho.validityPeriod]: 愛の手帳は3・6・12・18歳で年齢更新の再判定が必要です。
//   値: 3,6,12,18歳
// 出典: disability-notebooks
// 数値制約: 18歳

// C5 [MentalDisabilityHandbook.validityPeriod]: 精神障害者保健福祉手帳の有効期限は原則2年です。
//   値: 2年
// 出典: disability-notebooks

// C6 [MentalDisabilityHandbook.targetDisabilityCategory]: 精神障害者保健福祉手帳は知的障害のみの方は対象外です。
//   値: 知的障害のみNone
// 出典: disability-notebooks

// C7 [ChildWithDisability.age]: 障害児は18歳未満を指します。
//   値: 18歳未満
// 出典: target-categories
// 数値制約: 18歳

// C8 [BunkyoDisabilityWelfareDivision.faxNumber]: 障害福祉課のFAX番号は共通で03-5803-1352です。
//   値: 03-5803-1352None
// 出典: key-contacts

// C9 [BunkyoPreventionMeasuresDivision.faxNumber]: 予防対策課のFAX番号は03-5803-1355です。
//   値: 03-5803-1355None
// 出典: key-contacts
