import {useEffect,useState} from 'react';
import {api,explain} from '../api.js';
import {yuan} from '../format.js';
import {Drawer} from './ui.jsx';
import './FinancePanel.css';

const names={loan:'贷款',installment:'消费分期',gold_buy:'买入黄金',gold_sell:'卖出黄金'};
const states={DRAFT:'待确认',ACTIVE:'还款中',SETTLED:'已结清',COMPLETED:'已成交',ARCHIVED:'已归档'};
const operations={loan:'LOAN_DRAW',installment:'INSTALLMENT_START',gold_buy:'GOLD_BUY',gold_sell:'GOLD_SELL'};
const messages={SUITABILITY_RISK_MISMATCH:'黄金交易为 R3，请先在理财页完成适当的风险测评。风险等级不足时不能买入。',RISK_PROFILE_REQUIRED:'风险测评缺失或已过期，请先在理财页重新测评。',GOLD_QUOTE_UNAVAILABLE:'当前没有可用于成交的新鲜参考行情，请稍后刷新。不会使用离线快照成交。',QUOTE_EXPIRED:'报价已过期，请重新核对。',INSUFFICIENT_BALANCE:'所选银行卡余额不足。',CARD_NOT_PAYABLE:'卡片已冻结或线上交易已关闭。',CARD_LIMIT_EXCEEDED:'本笔金额超过银行卡限额。',CREDIT_LIMIT_EXCEEDED:'超过当前可用虚拟授信额度。',INSUFFICIENT_HOLDING:'黄金持仓不足。',ALREADY_FINANCED:'这笔消费已办理过分期。',STALE_PRECONDITION:'账户、持仓或订单已变化，请重新核对。',FINANCE_STATE_INVALID:'该业务已处理或状态不支持此操作。',BANK_PRECONDITION_CHANGED:'账户状态已变化，请重新发起确认。',ACTION_NOT_PENDING:'该确认已处理或过期。'};
function errorText(e){return messages[e.code]||e.message||explain(e)}
function PlanTable({rows}){return <div className="finance-table-wrap"><table className="finance-table"><thead><tr><th>期数 / 日期</th><th>本金</th><th>费用</th><th>应还</th><th>状态</th></tr></thead><tbody>{rows.map(r=><tr key={r.period}><td>第 {r.period} 期<small>{r.due}</small></td><td>{yuan(r.principal)}</td><td>{yuan(r.fee)}{r.waived_fee>0&&<small>减免 {yuan(r.waived_fee)}</small>}</td><td>{yuan(r.total)}</td><td>{r.paid?'已还':'待还'}</td></tr>)}</tbody></table></div>}

export default function FinancePanel({gold=false}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const [type,setType]=useState(gold?'gold_buy':'loan'),[amount,setAmount]=useState('1000'),[units,setUnits]=useState('1'),[months,setMonths]=useState('6'),[cardId,setCardId]=useState(''),[source,setSource]=useState('');
 const [showArchived,setShowArchived]=useState(false);
 const [draft,setDraft]=useState(null),[proposal,setProposal]=useState(null),[detail,setDetail]=useState(null),[code,setCode]=useState(''),[method,setMethod]=useState('SMS'),[sms,setSms]=useState(null);
 const reload=()=>api.get('/api/chat/finance').then(setData);
 useEffect(()=>{reload().catch(e=>setError(errorText(e)))},[]);
 async function run(fn){setBusy(true);setError('');setNotice('');try{await fn();await reload()}catch(e){setError(errorText(e))}finally{setBusy(false)}}
 const selected=data?.cards.find(c=>c.id===cardId);
 const sources=(data?.eligible||[]).filter(s=>s.account_id===selected?.account_id);
 const contracts=(data?.contracts||[]).filter(c=>(showArchived||c.status!=='ARCHIVED')&&(gold?c.type.startsWith('gold_'):["loan","installment"].includes(c.type)));
 const cardLabel=id=>{const c=data?.cards.find(c=>c.id===id);return (c?.design?.nickname||'虚拟银行卡')+' · 标识 '+id.slice(-4)};
 const confirm=proposal?.cards.find(c=>c.type==='confirm');
 async function prepare(t,operation=operations[t.type]){
  const p=await api.post('/api/chat/finance/proposal',{contract_id:t.id,operation});
  if(p.status!=='WAITING_USER'){const codes=(p.errors||[]).flat();throw {code:codes[0],message:codes.map(c=>messages[c]||c).join('；')||'暂时不能办理，请刷新后核对状态。'}}
  setDraft(t);setProposal(p);setDetail(null);setCode('');setSms(null);setMethod(p.cards.find(c=>c.type==='confirm')?.required_verification?.[0]||'SMS');
 }
 async function create(e){e.preventDefault();await run(async()=>{
  if(!cardId)throw {message:'请选择银行卡'};
  if(type==='loan'&&!/^\d+(\.\d{1,2})?$/.test(amount))throw {message:'请输入最多两位小数的金额'};
  if(gold&&!/^\d+(\.\d{1,2})?$/.test(units))throw {message:'请输入最多两位小数的克数'};
  const b={type,card_id:cardId,...(gold?{units:Math.round(Number(units)*100)}:type==='loan'?{amount:Math.round(Number(amount)*100),months:Number(months)}:{source_id:source,months:Number(months)})};
  const r=await api.post('/api/chat/finance/create',b);await prepare(r.contract);
 })}
 async function decide(approve){await run(async()=>{
  const r=await api.post('/api/chat/finance/decide',{task_id:confirm.task_id,action_id:confirm.action_id,action_hash:confirm.action_hash,approve,...(approve&&confirm.risk_level==='RED'?{verification:{method,code}}:{})});
  setProposal(null);setDraft(null);
  setNotice(r.status==='COMPLETED'?'办理成功，银行卡余额、业务记录和签名流水已同步。':r.status==='DENIED'?'已拒绝，本笔业务未执行。':'未完成资金操作，请刷新记录核对状态：'+r.status);
 })}
 async function sendSms(){await run(async()=>{setSms(await api.post('/api/chat/finance/sms',{task_id:confirm.task_id,action_id:confirm.action_id}))})}
 function cardReason(c){return c.frozen?'账户冻结':c.status!=='ACTIVE'?'卡片不可用':!c.online_enabled?'线上交易关闭':''}
 return <section className="section finance-panel">
  <div className="finance-heading"><h2>{gold?'黄金交易':'办理业务'}</h2><button className="btn small" disabled={busy} onClick={()=>run(reload)}>刷新</button></div>
  <p className="muted">{gold?'使用虚拟资金，按可用公开参考行情折算人民币成交，无实物交割。报价锁定 120 秒，手续费为 0；参考行情不是银行实盘买卖价。':data?.rules||'虚拟贷款与消费分期，所有放款、还款均需逐笔确认。'}</p>
  {error&&<p role="alert" className="finance-alert">{error}</p>}{notice&&<p role="status" className="finance-notice">{notice}</p>}
  {!data?<button className="btn" onClick={()=>run(reload)}>重新读取</button>:<>
  {!gold&&<div className="finance-summary"><span>可用虚拟授信 <b className="num">{yuan(Math.max(0,data.credit_limit-data.outstanding))}</b></span><span>待还本金 <b className="num">{yuan(data.outstanding)}</b></span></div>}
  {gold&&<p className="faint">黄金风险等级 R3 · 你的等级 {data.risk?'R'+data.risk.risk_level:'未测评'}。买入需要有效且匹配的风险测评，卖出保留可用。</p>}{gold&&<div className="finance-summary">{data.positions.length?data.positions.map(p=><span key={p.account_id}>{data.cards.find(c=>c.account_id===p.account_id)?.design?.nickname||"黄金账户"}<b className="num">{(p.units/100).toFixed(2)} 克</b><small>持仓成本 {yuan(p.cost)}</small></span>):<span>当前持仓 <b className="num">0.00 克</b></span>}</div>}
  <div className="segmented" role="group" aria-label={gold?'黄金交易方向':'融资类型'}>{(gold?['gold_buy','gold_sell']:['loan','installment']).map(t=><button key={t} aria-pressed={type===t} onClick={()=>{setType(t);setSource('')}}>{names[t]}</button>)}</div>
  <form className="panel finance-form" onSubmit={create}>
   <label>银行卡<select className="input" required value={cardId} onChange={e=>{setCardId(e.target.value);setSource('')}}><option value="">选择银行卡</option>{data.cards.map(c=><option key={c.id} value={c.id} disabled={!!cardReason(c)}>{c.design?.nickname||c.id} · 可用 {yuan(c.balance)}{cardReason(c)?' · '+cardReason(c):''}</option>)}</select></label>
   {gold?<label>交易重量（克）<input className="input num" inputMode="decimal" required value={units} onChange={e=>setUnits(e.target.value)}/></label>:type==='loan'?<label>贷款金额（人民币）<input className="input num" inputMode="decimal" required value={amount} onChange={e=>setAmount(e.target.value)}/><small>100～20,000 元，确认后到账所选卡</small></label>:<label>已入账消费<select className="input" required value={source} onChange={e=>setSource(e.target.value)}><option value="">{sources.length?'选择消费记录':'这张卡暂无可分期消费'}</option>{sources.map(s=><option key={s.id} value={s.id}>{s.merchant} · {yuan(s.amount)}</option>)}</select><small>仅已有消费记录可办理；分期本金返还原账户，生成待还计划，不重复计算消费。</small></label>}
   {!gold&&<label>分期期数<select className="input" value={months} onChange={e=>setMonths(e.target.value)}>{[3,6,12].map(n=><option key={n} value={n}>{n} 期</option>)}</select></label>}
   <button className="btn primary" disabled={busy||!cardId||(!gold&&type==='installment'&&!source)}>核对{names[type]}</button>
  </form>
  <div className="finance-heading"><h3>{gold?'交易记录':'我的还款计划'}</h3></div>
  {!contracts.length&&<p className="faint">尚无记录。核对后仍需你确认才会执行。</p>}
  <label className="faint"><input type="checkbox" checked={showArchived} onChange={e=>setShowArchived(e.target.checked)}/> 显示已归档草稿</label><div className="finance-contracts">{contracts.map(t=><article className="panel" key={t.id}><div className="kv"><b>{names[t.type]}</b><span>{t.status==='DRAFT'&&t.expires_at<Date.now()/1000?'已过期':states[t.status]}</span></div><p className="num">{gold?`${(t.units/100).toFixed(2)} 克 · `:''}{yuan(t.amount)}</p><small>{t.card_label||cardLabel(t.card_id)} · {new Date(t.created_at*1000).toLocaleString('zh-CN')}</small>{!gold&&<p>剩余本金 {yuan(t.remaining)} · 已收费用 {yuan(t.paid_fees)}</p>}{t.type==='gold_sell'&&t.status==='COMPLETED'&&<p>已实现盈亏 {yuan(t.realized_profit)}</p>}<div className="finance-actions"><button className="btn small" onClick={()=>{setDetail(t);setError('')}}>查看详情</button>{t.status==='DRAFT'&&t.expires_at>Date.now()/1000&&<button className="btn small" disabled={busy} onClick={()=>run(()=>prepare(t))}>继续核对</button>}{t.status==='DRAFT'&&<button className="btn small" disabled={busy} onClick={()=>run(()=>api.post('/api/chat/finance/archive',{contract_id:t.id}))}>取消并归档草稿</button>}{t.status==='ACTIVE'&&<><button className="btn small" disabled={busy} onClick={()=>run(()=>prepare(t,'CREDIT_REPAY'))}>偿还本期</button><button className="btn small" disabled={busy} onClick={()=>run(()=>prepare(t,'CREDIT_SETTLE'))}>提前结清</button></>}</div></article>)}</div>
  <p className="faint">放款、还款和黄金资金流水在「资金总览 → 记录」查看。账单消费统计仅计入利息和手续费，避免把本金往来重复算作消费。</p>
  </>}
  {detail&&<Drawer title={names[detail.type]+'详情'} onClose={()=>setDetail(null)}><p className="finance-id">{detail.id}</p><p>银行卡：{detail.card_label||cardLabel(detail.card_id)}</p><p>金额：{yuan(detail.amount)} · {states[detail.status]}</p>{detail.schedule.length?<PlanTable rows={detail.schedule}/>:<><p>重量：{(detail.units/100).toFixed(2)} 克</p><p>参考价：{yuan(detail.price)}/克</p><p>{detail.quote?.source} · {detail.quote?.as_of}</p></>}<p className="faint">仅虚拟资金。每次还款、交易需要再次确认。</p></Drawer>}
  {confirm&&<Drawer title={confirm.title} onClose={()=>!busy&&setProposal(null)}><div className="card tier-yellow"><div className="card-body"><dl className="fields">{confirm.fields.map((f,i)=><div className="finance-field" key={i}><dt>{f.label}</dt><dd>{f.value}</dd></div>)}</dl>{draft?.schedule.length>0&&<><p className="faint">原还款计划；提前结清以本次确认金额为准，未来期费用免除。</p><PlanTable rows={draft.schedule}/></>}<p>确认后才会执行。本次报价、账户状态或余额变化后需重新核对。</p>{confirm.risk_level==='RED'&&<div className="verify"><label>身份验证<select className="input" value={method} onChange={e=>setMethod(e.target.value)}>{(confirm.required_verification||[]).map(m=><option key={m} value={m}>{m==='SMS'?'短信验证码':m==='TOTP'?'认证器动态码':'测试验证口令'}</option>)}</select></label><label>验证码<input className="input" autoComplete="off" type="password" value={code} onChange={e=>setCode(e.target.value)}/></label>{method==='SMS'&&<button className="btn small" disabled={busy} onClick={sendSms}>获取验证码</button>}{sms?.dev_code&&<p>测试短信验证码：{sms.dev_code}</p>}</div>}{error&&<p role="alert" className="finance-alert">{error}</p>}<div className="finance-actions"><button className="btn primary" disabled={busy||(confirm.risk_level==='RED'&&!code)} onClick={()=>decide(true)}>确认办理</button><button className="btn" disabled={busy} onClick={()=>decide(false)}>拒绝</button></div></div></div></Drawer>}
 </section>
}
