import {useEffect,useRef,useState} from 'react';
import {api,auth,appPath} from '../api.js';

export default function ConnectionBanner(){
 const [state,setState]=useState('checking'),[busy,setBusy]=useState(false);const active=useRef(false);
 async function check(){
  if(active.current)return;active.current=true;setBusy(true);
  try{
   const response=await fetch(appPath('/health'),{signal:AbortSignal.timeout(8000),cache:'no-store'});
   if(!response.ok)throw new Error('offline');
   if(auth.token())await api.capabilities();
   setState('connected');
  }catch(e){setState([401,403].includes(e.status)?'auth':'offline')}
  finally{active.current=false;setBusy(false)}
 }
 useEffect(()=>{check();const timer=setInterval(check,30000);window.addEventListener('online',check);return()=>{clearInterval(timer);window.removeEventListener('online',check)}},[]);
 if(state==='connected'||state==='checking')return null;
 return <aside role="status" className="panel" style={{margin:'8px 16px',display:'flex',flexWrap:'wrap',gap:12,alignItems:'center'}}>
  <span style={{flex:'1 1 240px'}}>{state==='auth'?'此设备登录已失效，请重新配对。':'暂时连不上本地后台。请保持电脑开机联网，并启动 TRUSTA 与隧道。'}<small style={{display:'block'}}>若刚提交过交易，请恢复连接后查看记录；系统不会自动重复提交。</small></span>
  <button className="btn small" disabled={busy} onClick={check}>{busy?'检查中…':'检查连接'}</button>
  {state==='auth'&&<button className="btn small" onClick={()=>{auth.clear();location.reload()}}>重新配对</button>}
 </aside>;
}
