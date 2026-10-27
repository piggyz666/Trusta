import {useEffect,useState} from 'react';
import {api,explain} from '../api.js';
import {yuan} from '../format.js';
import './BankExtensions.css';
export default function Todos({navigate}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[filter,setFilter]=useState('all');
 async function reload(){setBusy(true);setError('');try{setData(await api.get('/api/chat/todos'))}catch(e){setError(explain(e))}finally{setBusy(false)}}
 useEffect(()=>{reload()},[]);
 const items=(data?.items||[]).filter(i=>filter==='all'||i.overdue);
 return <div className="page"><div className="inner bank-extensions"><header className="page-head"><h1>统一待办</h1><p>还款、存款到期、定投、基金订单和生活计划集中查看。提醒不会自动扣款。</p></header><div className="finance-actions"><button className="btn" disabled={busy} onClick={reload}>{busy?'正在更新…':'刷新'}</button><button className="btn" aria-pressed={filter==='all'} onClick={()=>setFilter('all')}>全部</button><button className="btn" aria-pressed={filter==='overdue'} onClick={()=>setFilter('overdue')}>已过日期</button></div>{error&&<p role="alert" className="finance-alert">{error}</p>}<div className="extension-list">{items.map(i=><article className="panel" key={i.id}><div className="kv"><b>{i.title}</b><span>{i.due||'待处理'}{i.overdue?' · 已过日期':''}</span></div><p>{i.detail}</p>{i.amount!=null&&<p className="num">{yuan(i.amount)}</p>}<button className="btn" onClick={()=>navigate(i.page)}>查看并办理</button></article>)}</div>{data&&!items.length&&<p className="muted">当前没有这类待办。</p>}<p className="faint">{data?.note}</p></div></div>;
}
