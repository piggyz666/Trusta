import {useEffect,useRef,useState} from 'react';
import {api} from '../api.js';

const currencies=[['USD','美元'],['EUR','欧元'],['JPY','日元'],['HKD','港币']];
function stamp(value){
 const d=new Date(value);
 return value&&!Number.isNaN(d.getTime())?d.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'更新时间未知';
}

export default function FxQuotes({currency,onSelect}){
 const [market,setMarket]=useState(null),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const inFlight=useRef(false),alive=useRef(false);
 async function load(force=false){
  if(inFlight.current)return;
  inFlight.current=true;setLoading(true);
  try{const value=await api.market(force);if(alive.current){setMarket(value);setError('')}}
  catch{if(alive.current)setError('行情暂时无法更新，请稍后刷新。已有数值仅作历史参考，不代表当前可成交价格。')}
  finally{inFlight.current=false;if(alive.current)setLoading(false)}
 }
 useEffect(()=>{
  alive.current=true;load();
  const timer=setInterval(()=>{if(document.visibilityState==='visible')load()},60000);
  return()=>{alive.current=false;clearInterval(timer)};
 },[]);
 return <section className="panel fx-quotes" aria-labelledby="fx-quotes-title" aria-busy={loading}>
  <div className="finance-heading"><div><h2 id="fx-quotes-title">外币参考汇率</h2><p className="muted">以人民币计价 · 数据源每日更新</p></div><button type="button" className="btn small" disabled={loading} onClick={()=>load(true)}>{loading?'正在更新…':'刷新汇率'}</button></div>
  {error&&<p className="finance-alert" role="status">{error}</p>}
  <div className="fx-quote-grid">{currencies.map(([code,name])=>{
   const q=market?.quotes?.find(item=>item.symbol===code+'CNY');
   const available=q&&!q.snapshot&&Number.isFinite(q.price)&&q.price>0;
   const outdated=Boolean(error||q?.stale);
   return <button type="button" key={code} className={'fx-quote'+(currency===code?' selected':'')} aria-pressed={currency===code} onClick={()=>onSelect(code)}>
    <span className="fx-quote-name">{name}<span className="faint">{code}</span></span>
    <strong className="num">{available?q.price.toFixed(4):'—'}</strong>
    <span className="muted">人民币 / {code==='JPY'?'100':'1'} {name}</span>
    <span className="fx-quote-state">{available?(outdated?'历史参考 · 暂不可用于核价':'公开参考价'):loading?'正在读取':'暂无可用行情'}</span>
    <small>{available?q.source:'未使用内置快照替代行情'}</small>
    <small>{available?'数据时间（北京时间）：'+stamp(q.as_of):'联网获取成功后显示'}</small>
   </button>;
  })}</div>
  <p className="faint rule-note">点击币种可同步选择兑换币种。页面每分钟检查更新；参考汇率可能延迟，并非银行实盘买卖价。兑换金额以核对页锁定的报价为准，过期或缺失行情不会自动换成练习价。</p>
 </section>;
}
