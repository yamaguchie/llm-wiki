// ── オントロジー定義から自動生成された制約 ──
// 生成日時: 2026-07-15T15:54:12.959537
// このファイルは ontology 再生成時に上書きされます。

// C1 [DisabilityNotebook.ReevaluationConditions]: 愛の手帳は3・6・12・18歳で年齢更新の再判定を受ける。
//   値: 3歳, 6歳, 12歳, 18歳年齢
// 出典: disability-notebooks
// 数値制約: 18歳

// C2 [DisabilityNotebook.ExpirationPeriod]: 精神障害者保健福祉手帳の有効期限は原則2年。
//   値: 2年年
// 出典: disability-notebooks

// C3 [DisabilityNotebook.RenewalConditions]: 精神障害者保健福祉手帳の更新は期限の3か月前から可能。
//   値: 3か月前期間
// 出典: disability-notebooks

// C4 [DisabilityNotebook.ExcludedConditions]: 知的障害のみ（精神障害なし）の方は精神障害者保健福祉手帳の対象外。
//   値: 知的障害のみ（精神障害なし）None
// 出典: disability-notebooks

// C5 [Service.Name]: 心身障害者等福祉手当、精神障害者福祉手当は重複受給不可。
//   値: 重複受給不可None
// 出典: 04-allowances-pensions

// C6 [Service.EligibilitySummary]: 心身障害者等福祉手当、精神障害者福祉手当は、65歳以上で手帳交付/1級認定された場合は原則受給不可。
//   値: 手帳交付/1級認定年齢が65歳以上は原則受給不可None
// 出典: 04-allowances-pensions
// 数値制約: 65歳

// C7 [Service.ExcludedConditions]: 心身障害者等福祉手当、精神障害者福祉手当は施設入所者は受給不可。
//   値: 施設入所者None
// 出典: 04-allowances-pensions

// C8 [Service.ExcludedConditions]: 心身障害者医療費助成（マル障）の適用除外条件。
//   値: 健保未加入, 生活保護受給中, 公費施設入所, 所得超過, 重度障害になった年齢が65歳以上, 65歳前日までに未申請, 後期高齢者医療で住民税課税None
// 出典: 05-medical

// C9 [Service.Name]: 日常生活用具の給付は、介護保険対象者は介護保険福祉用具が原則優先。
//   値: 介護保険対象者は介護保険福祉用具が原則優先None
// 出典: 06-assistive-devices

// C10 [Service.Name]: 都営交通の無料乗車券、民営バス割引はシルバーパスと併用不可。
//   値: シルバーパスと併用不可None
// 出典: 11-fare-discounts

// C11 [Service.Name]: 福祉避難所は、まず区立小中学校等の避難所へ避難が原則。
//   値: まず区立小中学校等の避難所へ避難が原則None
// 出典: 08-disaster-emergency
