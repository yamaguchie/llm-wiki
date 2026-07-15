# -*- coding: utf-8 -*-
"""Phase6: KGを埋め込んだ Agentic Search 質問チャット(index.html)を生成。
kg.json を単一の真実源とし、file:// で開いても動くよう KG をインライン化する。
実行: py -3.14 build_chat.py"""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
kg = json.load(open(os.path.join(HERE, "..", "graph", "kg.json"), encoding="utf-8"))
vec = json.load(open(os.path.join(HERE, "..", "rag", "vectors.json"), encoding="utf-8"))

TEMPLATE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>障害者福祉 Agentic Search チャット</title>
<style>
:root{--bg:#f6f7f9;--fg:#1b1f24;--card:#fff;--line:#e2e6ea;--accent:#2b6cb0;--muted:#667;--user:#2b6cb0;--bot:#eef2f7;--trace:#fbfaf5;--warn:#b7791f;--ok:#2f855a}
@media(prefers-color-scheme:dark){:root{--bg:#14181d;--fg:#e6e9ec;--card:#1c2127;--line:#2c333b;--accent:#63b3ed;--muted:#98a2b3;--user:#2c5282;--bot:#232a32;--trace:#1a1f18;--warn:#d69e2e;--ok:#68d391}}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN",Meiryo,sans-serif;background:var(--bg);color:var(--fg);line-height:1.6}
.wrap{max-width:900px;margin:0 auto;padding:16px}
header h1{font-size:1.15rem;margin:.2em 0}
header p{color:var(--muted);font-size:.83rem;margin:.2em 0 1em}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.chip{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:5px 11px;font-size:.8rem;cursor:pointer}
.chip:hover{border-color:var(--accent);color:var(--accent)}
#log{display:flex;flex-direction:column;gap:12px;margin-bottom:14px}
.msg{max-width:88%;padding:10px 13px;border-radius:12px;font-size:.9rem;white-space:normal}
.me{align-self:flex-end;background:var(--user);color:#fff;border-bottom-right-radius:3px}
.bot{align-self:flex-start;background:var(--bot);border:1px solid var(--line);border-bottom-left-radius:3px}
.bot h3{margin:.1em 0 .4em;font-size:.95rem}
.bot ul{margin:.3em 0;padding-left:1.2em}
.bot li{margin:.25em 0}
.badge{display:inline-block;font-size:.7rem;padding:1px 7px;border-radius:999px;border:1px solid var(--line);margin-left:5px;vertical-align:middle}
.badge.inc{color:var(--warn);border-color:var(--warn)}
.badge.free{color:var(--ok);border-color:var(--ok)}
.src{font-size:.72rem;color:var(--muted);margin-top:2px}
.src a{color:var(--accent);text-decoration:none}
.warnline{color:var(--warn);font-size:.78rem}
details.trace{align-self:flex-start;max-width:88%;background:var(--trace);border:1px dashed var(--line);border-radius:10px;padding:6px 11px;font-size:.78rem;color:var(--muted)}
details.trace summary{cursor:pointer;font-weight:600}
.step{margin:6px 0;padding-left:8px;border-left:3px solid var(--line)}
.step b{color:var(--fg)}
.tnode{display:inline-block;font-size:.68rem;background:var(--card);border:1px solid var(--line);border-radius:5px;padding:0 6px;margin-right:5px}
.inrow{display:flex;gap:8px;margin-top:6px}
#q{flex:1;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg);font-size:.9rem}
#send{padding:10px 16px;border:0;border-radius:10px;background:var(--accent);color:#fff;font-weight:600;cursor:pointer}
.disc{color:var(--muted);font-size:.72rem;margin-top:10px;border-top:1px solid var(--line);padding-top:8px}
</style>
</head>
<body>
<div class="wrap">
<header>
<h1>障害者福祉 Agentic Search チャット</h1>
<p>文京区 令和7年 障害者福祉のてびき LLM-Wiki → ナレッジグラフ(<b id="ncount"></b>ノード/<b id="ecount"></b>エッジ)への反復検索デモ。<br>
質問を分解 → <b>ベクトル入口(意味検索)＋グラフ探索の2段ハイブリッド</b> → 十分性評価 → 不足なら再検索、という Agentic Search ループを可視化します。</p>
</header>
<div class="chips" id="chips"></div>
<div id="log"></div>
<div style="margin:6px 0"><label style="font-size:.82rem;cursor:pointer"><input type="checkbox" id="gmode"> 🤖 Geminiモード（LLMプランナー/クリティック/回答＋実ベクトル埋め込み。要 <code>server.py</code> / ポート8790）</label></div>
<div class="inrow">
  <input id="q" placeholder="例: 身体障害者手帳2級で受けられる手当は？" autocomplete="off">
  <button id="send">送信</button>
</div>
<div class="disc">※内容は原本＝令和7年5月末時点。金額・等級・時間上限は<b>human_required（要一次情報確認）</b>。本デモのプランナー/クリティックはルールベース（本番はLangGraph+LLMノードに置換）。回答の根拠リンクはLLM-Wikiの該当ページ。</div>
</div>
<script>
const KG = __KG_JSON__;
const VEC = __VEC_JSON__;
const N = {}; KG.nodes.forEach(n=>N[n.id]=n);
const E = KG.edges;
document.getElementById('ncount').textContent = KG.nodes.length;
document.getElementById('ecount').textContent = E.length;

// ---------- graph helpers ----------
const P = id => (N[id]?N[id].props:{});
const from = (id,t)=>E.filter(e=>e.from===id && (!t||e.type===t));
const into = (id,t)=>E.filter(e=>e.to===id && (!t||e.type===t));
const isSvc = id => N[id]&&N[id].labels.includes('Service');
function servicesByNotebookGrade(nb,grade,cat){
  const out=new Set();
  E.forEach(e=>{ if(e.type==='REQUIRES'&&e.to===nb){
    const gs=(e.props&&e.props.grades)||[];
    if(grade==='ALL'||gs.includes(grade)){
      if(cat && !from(e.from,'HAS_CATEGORY').some(x=>x.to===cat))return;
      out.add(e.from);
    }
  }});
  return [...out];
}
const mutual=(a,b)=>E.some(e=>e.type==='MUTUALLY_EXCLUSIVE_WITH'&&((e.from===a&&e.to===b)||(e.from===b&&e.to===a)));

// ---------- intent parsing ----------
const SVC_SYN=[['心身障害者等福祉手当','svc_shinshin_teate'],['精神障害者福祉手当','svc_seishin_teate'],
['特別児童扶養手当','svc_tokubetsu_jido'],['特別障害者手当','svc_tokubetsu_shogaisha'],['障害児福祉手当','svc_shogaiji_teate'],
['重度心身障害者手当','svc_judo_shinshin'],['児童育成手当','svc_jido_ikusei'],
['心身障害者医療費助成','svc_marusho'],['マル障','svc_marusho'],['更生医療','svc_kosei_iryo'],['精神通院','svc_seishin_tsuin'],
['難病医療','svc_nanbyo_iryo'],['難病の医療','svc_nanbyo_iryo'],
['補装具','svc_hosougu'],['日常生活用具','svc_nichijo_yogu'],['紙おむつ','svc_kamiomutsu'],
['盲導犬','svc_hojoken'],['聴導犬','svc_hojoken'],['補助犬','svc_hojoken'],
['同行援護','svc_ido_shien'],['移動支援','svc_ido_shien'],['レスパイト','svc_respite'],
['社会体験','svc_shakai_taiken'],['マイポジション','svc_shakai_taiken'],['ひまわり','svc_himawari'],['居宅訪問型保育','svc_kyotaku_hoiku'],
['福祉タクシー','svc_fukushi_taxi'],['燃料費','svc_fukushi_taxi'],['都営交通','svc_toei_free'],['無料乗車券','svc_toei_free'],
['有料道路','svc_yuryo_road'],['タクシー運賃','svc_taxi_wari'],['タクシー割引','svc_taxi_wari']];
const CAT_SYN=[['福祉用具','cat_device'],['日常生活','cat_daily'],['医療費','cat_medical'],['医療','cat_medical'],
['手当','cat_allowance'],['年金','cat_allowance'],['交通','cat_transport'],['割引','cat_transport'],['権利','cat_rights']];
const NB_SYN=[['身体障害者手帳','nb_body'],['身体手帳','nb_body'],['愛の手帳','nb_ai'],['療育手帳','nb_ai'],
['精神障害者保健福祉手帳','nb_mental'],['精神手帳','nb_mental']];
const zen="０１２３４５６７８９",kan="〇一二三四五六七八九";
function norm(s){let r=s;for(let i=0;i<10;i++){r=r.replaceAll(zen[i],i).replaceAll(kan[i],i);}return r;}
function detectNotebook(q){
  for(const[k,v]of NB_SYN)if(q.includes(k))return v;
  if(q.includes('身体'))return 'nb_body';
  if(q.includes('知的')||q.includes('愛の'))return 'nb_ai';
  if(q.includes('精神'))return 'nb_mental';
  return null;
}
function detectGrade(q,nb){
  let m=q.match(/([1-6])\s*級/); if(m)return m[1];
  m=q.match(/([1-4])\s*度/); if(m)return m[1];
  return undefined;
}
function detectServices(q){const hits=[];for(const[k,v]of SVC_SYN){if(q.includes(k)&&!hits.includes(v))hits.push(v);}return hits;}
function detectCategories(q){const hits=[];for(const[k,v]of CAT_SYN){if(q.includes(k)&&!hits.includes(v))hits.push(v);}return hits;}

// ---------- vector entry (Phase5: 意味検索でグラフの起点ノードを推定) ----------
function ngrams(s){s=norm(s).replace(/\s+/g,'');const r=[];for(let n=2;n<=3;n++)for(let i=0;i+n<=s.length;i++)r.push(s.slice(i,i+n));return r;}
function vectorEntry(q,k){
  k=k||3; const tf={}; ngrams(q).forEach(g=>tf[g]=(tf[g]||0)+1);
  const qv={}; let qn=0;
  for(const g in tf){const idf=VEC.idf[g]; if(!idf)continue; const w=tf[g]*idf; qv[g]=w; qn+=w*w;}
  qn=Math.sqrt(qn)||1e-9;
  const out=[];
  for(const id in VEC.nodes){const nd=VEC.nodes[id]; let dot=0;
    for(const g in qv){const w=nd.vec[g]; if(w)dot+=qv[g]*w;}
    out.push([id, dot/(qn*(nd.norm||1e-9))]);}
  out.sort((a,b)=>b[1]-a[1]); return out.slice(0,k);
}

function parseIntent(raw){
  const q=norm(raw);
  const nb=detectNotebook(q), grade=detectGrade(q,nb);
  const svcs=detectServices(q), cats=detectCategories(q);
  const forT = q.includes('難病')?'難病':q.includes('精神')?'精神':q.includes('知的')?'知的':null;
  return {raw,q,nb,grade,svcs,cats,forT,
    mcc:q.includes('医療的ケア児'),
    abuse:q.includes('虐待'),
    freehours:/(何時間|時間|無料|負担)/.test(q)&&(q.includes('移動支援')||q.includes('同行援護')),
    compat:/(併給|両方|重複|一緒|同時)/.test(q),
    exclusion:/(受けられない|受給できない|対象外|もらえない|除外)/.test(q),
    contactq:/(窓口|どこ|連絡|電話|問い合わせ|申請先|申請する)/.test(q)};
}

// ---------- planner (分解要否判断) ----------
function planner(it){
  if(it.abuse)return[{kind:'abuse'}];
  if(it.mcc)return[{kind:'mcc'}];
  if(it.compat&&it.svcs.length>=2)return[{kind:'compat',a:it.svcs[0],b:it.svcs[1]}];
  if(it.freehours)return[{kind:'free_hours',service:'svc_ido_shien'}];
  if(it.nb){
    if(it.cats.length>=2)return it.cats.map(c=>({kind:'eligible',nb:it.nb,grade:it.grade,cat:c}));
    if(it.cats.length===1)return[{kind:'eligible',nb:it.nb,grade:it.grade,cat:it.cats[0]}];
    return[{kind:'eligible',nb:it.nb,grade:it.grade}];
  }
  // keyword入口が空振りなら Vector入口(意味検索)で起点サービスを補う = 2段ハイブリッド
  let svcs=it.svcs, via=null;
  if(!svcs.length){
    const ve=vectorEntry(it.raw,3);
    if(ve.length && ve[0][1]>=0.08){ svcs=[ve[0][0]]; via={seeds:ve}; }
  }
  if(svcs.length){
    let sg = it.contactq?{kind:'contact',service:svcs[0],forT:it.forT}
           : it.exclusion?{kind:'exclusion',service:svcs[0]}
           : {kind:'service_detail',service:svcs[0]};
    if(via) sg._via=via;
    return [sg];
  }
  if(it.cats.length)return[{kind:'category',cat:it.cats[0]}];
  const ve2=vectorEntry(it.raw,3);
  if(ve2.length && ve2[0][1]>=0.05) return[{kind:'service_detail',service:ve2[0][0],_via:{seeds:ve2}}];
  return[{kind:'keyword',text:it.q}];
}

// ---------- retrieval (ツール選択) ----------
function retrieve(sg){
  let facts=[],touched=[],tool='';
  if(sg.kind==='eligible'){
    tool='graph:REQUIRES多ホップ';
    const g=(sg.grade===undefined)?'ALL':sg.grade;
    const ids=servicesByNotebookGrade(sg.nb,g,sg.cat);
    touched=[sg.nb,...(sg.cat?[sg.cat]:[]),...ids];
    facts=ids.map(id=>({t:'service',id})); facts._grade=sg.grade;
  }else if(sg.kind==='free_hours'){
    tool='graph:プロパティ参照'; touched=[sg.service];
    facts=[{t:'free',id:sg.service,hours:P(sg.service).free_hours_per_month}];
  }else if(sg.kind==='service_detail'){
    tool='graph:近傍展開'; touched=[sg.service,...from(sg.service).map(e=>e.to)];
    facts=[{t:'detail',id:sg.service}];
  }else if(sg.kind==='contact'){
    tool='graph:ADMINISTERED_BY';
    let ad=from(sg.service,'ADMINISTERED_BY');
    if(sg.forT)ad=ad.filter(e=>(e.props.for||'').includes(sg.forT));
    touched=[sg.service,...ad.map(e=>e.to)];
    facts=ad.map(e=>({t:'contact',id:e.to,for:e.props.for,hours:e.props.hours,svc:sg.service}));
  }else if(sg.kind==='compat'){
    tool='graph:MUTUALLY_EXCLUSIVE_WITH'; touched=[sg.a,sg.b];
    const m=E.find(e=>e.type==='MUTUALLY_EXCLUSIVE_WITH'&&((e.from===sg.a&&e.to===sg.b)||(e.from===sg.b&&e.to===sg.a)));
    facts=[{t:'compat',a:sg.a,b:sg.b,excl:!!m,basis:m?m.props.basis:null}];
  }else if(sg.kind==='mcc'){
    tool='graph:プロパティフィルタ';
    const ids=KG.nodes.filter(n=>isSvc(n.id)&&n.props.medical_care_child).map(n=>n.id);
    touched=ids; facts=ids.map(id=>({t:'service',id}));
  }else if(sg.kind==='category'){
    tool='graph:HAS_CATEGORY'; const ids=into(sg.cat,'HAS_CATEGORY').map(e=>e.from);
    touched=[sg.cat,...ids]; facts=ids.map(id=>({t:'service',id}));
  }else if(sg.kind==='exclusion'){
    tool='graph:プロパティ参照'; touched=[sg.service];
    facts=[{t:'exclude',id:sg.service}];
  }else if(sg.kind==='abuse'){
    tool='graph:ADMINISTERED_BY(hours)';
    const ad=from('svc_gyakutai','ADMINISTERED_BY'); touched=['svc_gyakutai',...ad.map(e=>e.to)];
    facts=ad.map(e=>({t:'contact',id:e.to,for:e.props.for,hours:e.props.hours,svc:'svc_gyakutai'}));
  }else{ // keyword
    tool='fallback:語彙マッチ';
    const ids=KG.nodes.filter(n=>isSvc(n.id)&&(n.props.name.includes(sg.text)||JSON.stringify(n.props).includes(sg.text))).map(n=>n.id);
    touched=ids; facts=ids.map(id=>({t:'service',id}));
  }
  return {facts,touched,tool};
}

// ---------- critic (evidence-gap) ----------
function critic(sg,facts){
  if(sg.kind==='eligible'){
    if(sg.grade===undefined) return {ok:false,missing:'grade',msg:'手帳の等級が未指定'};
    if(facts.length===0) return {ok:false,missing:'match',msg:'該当サービスが0件'};
  }
  if(sg.kind==='contact'){
    if(sg.forT && facts.length===0) return {ok:false,missing:'forT',msg:`対象「${sg.forT}」の窓口が特定できない`};
    if(facts.length===0) return {ok:false,missing:'match',msg:'窓口が0件'};
  }
  if(['service_detail','category','mcc','keyword'].includes(sg.kind) && facts.length===0)
    return {ok:false,missing:'match',msg:'該当0件'};
  return {ok:true};
}
// ---------- rewrite ----------
function rewrite(sg,crit){
  const s={...sg};
  if(crit.missing==='grade'){s.grade='ALL';s._note='等級未指定のため全等級で再検索';}
  else if(crit.missing==='forT'){s.forT=null;s._note='対象フィルタを外して再検索';}
  else if(crit.missing==='match'){return {kind:'keyword',text:(sg.text||P(sg.service||'').name||sg.raw||''),_note:'語彙検索にフォールバック'};}
  return s;
}

// ---------- agentic loop ----------
const MAX=4;
function agentic(raw){
  const it=parseIntent(raw), trace=[]; let plan=planner(it);
  trace.push({node:'Planner',txt:`分解: ${plan.length}サブゴール — ${plan.map(p=>p.kind).join(', ')}`});
  const all=[];
  plan.forEach((sg0,idx)=>{
    let sg=sg0, round=0;
    if(sg0._via){ trace.push({node:'VectorEntry(意味検索)',txt:'grep辞書に無い言い方 → 埋め込み類似で起点を推定: '+sg0._via.seeds.map(function(x){return P(x[0]).name+'('+x[1].toFixed(2)+')';}).join(' , '),touched:sg0._via.seeds.map(function(x){return x[0];})}); }
    while(round<MAX){
      round++;
      const r=retrieve(sg);
      trace.push({node:'Retrieval',txt:`SG${idx+1} R${round} [${r.tool}] 取得${r.facts.length}件`,touched:r.touched});
      const c=critic(sg,r.facts);
      trace.push({node:'EvidenceGap',txt:c.ok?`十分 ✓`:`不十分 ✗ — ${c.msg}`});
      r.facts.forEach(f=>all.push(f));
      if(c.ok){break;}
      const nx=rewrite(sg,c);
      trace.push({node:'Rewrite',txt:nx._note||'再検索'});
      sg=nx;
    }
  });
  return {answer:compose(it,plan,all),trace};
}

// ---------- compose answer ----------
function srcChip(id){const s=P(id).src;if(!s)return '';const wp=s.wiki?s.wiki.split('/').pop().replace('.md',''):'';
  return `<div class="src">根拠: <a href="../../${s.wiki}" title="LLM-Wiki">${wp}</a>${s.booklet_p?` / 原本${s.booklet_p}p`:''}</div>`;}
function tiersHtml(id){return P(id).benefit_tiers.map(t=>{
  let a=(typeof t.amount==='number')?t.amount.toLocaleString()+'円':t.amount;
  return `${t.criteria} → <b>${a}</b>${t.freq&&t.freq!=='—'?`（${t.freq}）`:''}`;}).join('<br>');}
function contactHtml(id,forT,hours){const p=P(id);
  return `${p.dept} ☎${p.phone}${forT?`（対象:${forT}）`:''}${hours?`／${hours}`:''}`;}
function svcCard(id){const p=P(id);
  const admins=from(id,'ADMINISTERED_BY').map(e=>'　- '+contactHtml(e.to,e.props.for,e.props.hours)).join('<br>');
  const reqs=from(id,'REQUIRES').map(e=>{const nb=P(e.to);const gs=(e.props.grades||[]).join('・');
    return `${nb.name}${gs?` ${gs}${nb.grade_type}`:''}${e.props.grade_note?` <span class="warnline">※${e.props.grade_note}</span>`:''}`;}).join(' / ');
  const defs=from(id,'DEFINED_BY').map(e=>`${P(e.to).name}（原本${P(e.to).booklet_page}p）`).join('、');
  return `<h3>${p.name}${p.income_limit?'<span class="badge inc">所得制限あり</span>':''}${p.free_hours_per_month?`<span class="badge free">月${p.free_hours_per_month}h無料</span>`:''}</h3>`+
    `<div><b>内容</b>: ${tiersHtml(id)}</div>`+
    (reqs?`<div><b>対象手帳</b>: ${reqs}</div>`:'')+
    (p.age_range?`<div><b>対象年齢</b>: ${p.age_range}</div>`:'')+
    (p.note?`<div class="warnline">※${p.note}</div>`:'')+
    (admins?`<div><b>窓口</b>:<br>${admins}</div>`:'')+
    (p.excludes?`<div><b>受給できない方</b>: ${p.excludes.join('／')}</div>`:'')+
    (defs?`<div><b>参照</b>: ${defs}</div>`:'')+
    srcChip(id);
}
function compose(it,plan,facts){
  const kinds=new Set(plan.map(p=>p.kind));
  // dedupe service facts
  const svcIds=[...new Set(facts.filter(f=>f.t==='service').map(f=>f.id))];
  if(kinds.has('free_hours')){
    const f=facts.find(x=>x.t==='free');
    return `<h3>利用者負担が無料になる上限</h3><div>同行援護・移動支援は<b>月${f.hours}時間まで</b>利用者負担がかかりません（区の軽減制度）。</div>${srcChip('svc_ido_shien')}`;
  }
  if(kinds.has('compat')){
    const f=facts.find(x=>x.t==='compat');
    return `<h3>併給可否</h3><div>「${P(f.a).name}」と「${P(f.b).name}」は<b>${f.excl?'併給できません':'併給の制限は登録されていません'}</b>。</div>`+
      (f.basis?`<div class="warnline">根拠: ${f.basis}</div>`:'')+srcChip(f.a);
  }
  if(kinds.has('abuse')){
    const cs=facts.filter(f=>f.t==='contact');
    return `<h3>障害者虐待の通報先</h3><ul>`+cs.map(c=>`<li>${contactHtml(c.id,null,c.hours)}</li>`).join('')+`</ul>`+
      `<div class="warnline">気づいた人に通報義務（匿名可・守秘義務で保護）。</div>${srcChip('svc_gyakutai')}`;
  }
  if(kinds.has('contact')){
    const cs=facts.filter(f=>f.t==='contact');
    if(!cs.length)return `<div>窓口を特定できませんでした。</div>`;
    const sid=cs[0].svc;
    return `<h3>${P(sid).name} の窓口</h3><ul>`+cs.map(c=>`<li>${contactHtml(c.id,c.for,c.hours)}</li>`).join('')+`</ul>`+
      from(sid,'DEFINED_BY').map(e=>`<div><b>対象疾病/限度額の確認</b>: ${P(e.to).name}（原本${P(e.to).booklet_page}p）</div>`).join('')+srcChip(sid);
  }
  if(kinds.has('exclusion')){
    const id=facts.find(f=>f.t==='exclude').id;
    return `<h3>${P(id).name} を受けられない方</h3><ul>`+P(id).excludes.map(x=>`<li>${x}</li>`).join('')+`</ul>`+srcChip(id);
  }
  if(kinds.has('service_detail')){
    return svcIds.length?svcIds.map(svcCard).join('<hr style="border:0;border-top:1px solid var(--line);margin:8px 0">'):
      (facts.filter(f=>f.t==='detail').map(f=>svcCard(f.id)).join('')||'<div>該当なし</div>');
  }
  // eligible / mcc / category / keyword → サービス一覧
  const detailIds=[...new Set([...svcIds,...facts.filter(f=>f.t==='detail').map(f=>f.id)])];
  if(!detailIds.length)return '<div>該当するサービスが見つかりませんでした。質問を具体化してみてください。</div>';
  let head='該当するサービス';
  if(kinds.has('eligible')){const g=it.grade!==undefined?`${it.grade}${P(it.nb).grade_type}`:'（等級指定なし＝全等級）';head=`${P(it.nb).name}${g} の方が対象になり得るサービス`;}
  if(kinds.has('mcc'))head='医療的ケア児が利用できるサービス';
  if(kinds.has('category'))head=`「${P(it.cats[0]).name}」カテゴリのサービス`;
  return `<h3>${head}（${detailIds.length}件）</h3>`+
    detailIds.map(id=>{const p=P(id);
      const admins=from(id,'ADMINISTERED_BY').map(e=>contactHtml(e.to,e.props.for)).join(' / ');
      const gn=from(id,'REQUIRES').map(e=>e.props.grade_note).filter(Boolean);
      return `<div style="margin:.5em 0"><b>${p.name}</b>${p.income_limit?'<span class="badge inc">所得制限</span>':''}`+
        `<br><span style="font-size:.85em">内容: ${tiersHtml(id)}</span>`+
        (gn.length?`<br><span class="warnline">※${gn.join('／')}</span>`:'')+
        `<br><span style="font-size:.8em;color:var(--muted)">窓口: ${admins}</span>${srcChip(id)}</div>`;}).join('');
}

// ---------- UI ----------
const log=document.getElementById('log');
function bubble(cls,html){const d=document.createElement('div');d.className='msg '+cls;d.innerHTML=html;log.appendChild(d);d.scrollIntoView({behavior:'smooth',block:'end'});return d;}
function traceBox(trace){
  const d=document.createElement('details');d.className='trace';
  d.innerHTML=`<summary>🔎 エージェント・トレース（${trace.length}ステップ）</summary>`+
    trace.map(s=>`<div class="step"><span class="tnode">${s.node}</span><b>${s.txt}</b>`+
      (s.touched&&s.touched.length?`<div style="font-size:.72rem">touched: ${s.touched.slice(0,8).map(id=>(P(id).name||P(id).dept||id)).join(' , ')}${s.touched.length>8?' …':''}</div>`:'')+`</div>`).join('');
  log.appendChild(d);d.scrollIntoView({behavior:'smooth',block:'end'});
}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function mdToHtml(md){
  const lines=(md||'').split('\n'); let html='',inList=false;
  const inline=s=>s.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
  for(const raw of lines){
    const line=raw.replace(/\s+$/,'');
    if(/^\s*[-*]\s+/.test(line)){ if(!inList){html+='<ul>';inList=true;} html+='<li>'+inline(esc(line.replace(/^\s*[-*]\s+/,'')))+'</li>'; continue; }
    if(inList){html+='</ul>';inList=false;}
    if(/^#{1,6}\s+/.test(line)){ html+='<h3>'+inline(esc(line.replace(/^#{1,6}\s+/,'')))+'</h3>'; continue; }
    if(/^---+$/.test(line)){ html+='<hr style="border:0;border-top:1px solid var(--line);margin:8px 0">'; continue; }
    if(line.trim()===''){ html+='<div style="height:4px"></div>'; continue; }
    html+='<div>'+inline(esc(line))+'</div>';
  }
  if(inList)html+='</ul>';
  return html;
}
function gmode(){const e=document.getElementById('gmode');return e&&e.checked;}
async function ask(q){
  if(!q.trim())return;
  bubble('me',esc(q));
  if(gmode()){
    const pend=bubble('bot','⏳ Gemini が思考中…（Planner→VectorSeed→Graph→Critic→Answer）');
    try{
      const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})});
      const d=await r.json();
      pend.remove();
      if(d.error){ bubble('bot','⚠ エラー: '+esc(d.error)); return; }
      traceBox(d.trace);
      bubble('bot',mdToHtml(d.answer));
    }catch(e){ pend.remove(); bubble('bot','⚠ 通信エラー: '+esc(String(e))+'<br>（Geminiモードは <code>server.py</code> を起動し <b>ポート8790</b> で開く必要があります）'); }
    return;
  }
  const {answer,trace}=agentic(q);
  traceBox(trace);
  bubble('bot',answer);
}
document.getElementById('send').onclick=()=>{const i=document.getElementById('q');ask(i.value);i.value='';};
document.getElementById('q').addEventListener('keydown',e=>{if(e.key==='Enter'){document.getElementById('send').click();}});
const SAMPLES=['身体障害者手帳2級で受けられる手当は？','精神障害者保健福祉手帳1級で使える手当と医療の助成は？',
'難病の人が補装具を申請する窓口はどこ？対象疾病はどこで確認できる？','医療的ケア児が使えるサービスを全部教えて',
'移動支援は月何時間まで無料？','精神障害者福祉手当と心身障害者等福祉手当は併給できる？',
'心身障害者等福祉手当を受けられないのはどんな人？','障害者虐待に気づいたら夜間はどこに通報する？',
'身体2級で使える交通の割引は？',
'車いすの部品を作り直す費用の補助はどこ？','うつ病で通院してるけど医療費の助成ある？','外出のとき付き添ってくれるヘルパーの窓口は？'];
const chips=document.getElementById('chips');
SAMPLES.forEach(s=>{const b=document.createElement('button');b.className='chip';b.textContent=s;b.onclick=()=>ask(s);chips.appendChild(b);});
bubble('bot','こんにちは。文京区の障害者福祉サービスについて、ナレッジグラフを反復探索してお答えします。上のサンプル質問をクリックするか、自由に入力してください。');
</script>
</body>
</html>
"""

html = TEMPLATE.replace("__KG_JSON__", json.dumps(kg, ensure_ascii=False)).replace("__VEC_JSON__", json.dumps(vec, ensure_ascii=False))
open(os.path.join(HERE, "index.html"), "w", encoding="utf-8").write(html)
print("wrote index.html", len(html), "bytes")
