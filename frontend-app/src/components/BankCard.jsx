import { T, isSandbox } from '../bankMode.js';
import { useState } from 'react';
import { api, explain } from '../api.js';
import { yuan } from '../format.js';
import { Drawer, useToast } from './ui.jsx';
import './BankCard.css';
export const CARD_THEMES=[['midnight','曜石黑'],['champagne','香槟金'],['ruby','绯红'],['ocean','深海蓝']];
export function BankCard({card,compact=false}) {
 const theme=card.design?.theme || 'midnight';
 return <article className={`bank-face bank-face-${theme} ${compact?'bank-face-compact':''}`} aria-label={card.design?.nickname || '虚拟银行卡'}>
  <div className="bank-face-top"><b>TRUSTA</b><span>{T('虚拟借记卡')}</span></div>
  <div className="bank-face-chip" aria-hidden="true"><i/><i/><i/></div>
  <div className="bank-face-name">{card.design?.nickname || (card.account_id?.includes('saving')?'储蓄卡':'生活卡')}</div>
  <div className="bank-face-balance"><small>可用余额</small><strong>{yuan(card.balance ?? 0)}</strong></div>
  <div className="bank-face-bottom"><span>卡标识 · {card.id}</span><span>{card.status==='ACTIVE'?'正常':card.status==='FROZEN'?'已冻结':card.status==='LOST'?'已挂失':card.status}</span></div>
 </article>;
}
export function CardDesigner({card,onClose,onSaved}) {
 const [theme,setTheme]=useState(card.design?.theme||'midnight');const [nickname,setNickname]=useState(card.design?.nickname||'');const [busy,setBusy]=useState(false);const toast=useToast();
 async function save(){setBusy(true);try{await api.post('/api/chat/retail/design',{card_id:card.id,theme,nickname});onSaved?.();onClose();}catch(e){toast(explain(e),'error')}finally{setBusy(false)}}
 return <Drawer title="定制卡面" onClose={onClose}><BankCard card={{...card,design:{theme,nickname}}}/><label className="card-design-label">卡片名称<input className="input" maxLength={20} value={nickname} onChange={e=>setNickname(e.target.value)} placeholder="例如：日常生活卡"/></label><div className="card-theme-options" role="group" aria-label="卡面颜色">{CARD_THEMES.map(([id,title])=><button className="btn small" aria-pressed={theme===id} key={id} onClick={()=>setTheme(id)}>{title}</button>)}</div><p className="faint">卡面和名称仅用于展示，不会改变余额、付款账户或交易权限。</p><button className="btn primary" disabled={busy} onClick={save}>{busy?'保存中…':'保存卡面'}</button></Drawer>;
}
