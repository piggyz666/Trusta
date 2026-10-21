"use strict";
const $ = id => document.getElementById(id);
const fragmentToken = new URLSearchParams(location.hash.slice(1)).get("token");
if (fragmentToken) { sessionStorage.setItem("trusta-token", fragmentToken); history.replaceState(null, "", "/"); }
const token = sessionStorage.getItem("trusta-token") || "";
let task = null;
let busy = false;
let conversation = null;
let auditProof = null;
let bank = null;
let payees = [];
let sourceAccount = sessionStorage.getItem('trusta-source-account') || 'acct-demo';
const names = ["解析目标", "核对收款人", "核对付款账户", "读取余额", "生成转账提案", "评估规则", "等待用户授权", "执行转账", "核验银行结果", "生成回执"];
const readStepNames = {F01:"解析分析请求",F02:"读取账户",F03:"读取消费记录",F04:"分类与月度分析",F05:"异常交易检测",F06:"发布分析报告",
  W01:"解析推荐请求",W02:"读取风险等级",W03:"读取产品库",W04:"匹配适合产品",W05:"生成推荐理由",W06:"发布推荐报告"};
const labels = {PENDING:"待处理",READY:"就绪",RUNNING:"处理中",WAITING_USER:"待确认",COMPLETED:"完成",FAILED:"失败",BLOCKED:"已阻止",MANUAL_REVIEW:"待人工核查",CANCELLED:"已取消",EXECUTING:"执行中",VERIFYING:"核验中"};
const money = (cents,currency='CNY') => (cents / 100).toLocaleString("zh-CN", {minimumFractionDigits:2,maximumFractionDigits:2}) + ' ' + (currency==='CNY' ? '元' : currency);
function applyBankControls() {
  const external=bank?.environment!=='local';
  $('example-transfer').disabled=busy || (external && (!bank?.writes_enabled || !payees.some(p=>p.currency===bank?.accounts.find(a=>a.id===sourceAccount)?.currency)));
  $('example-wealth').disabled=busy || !bank?.capabilities.includes('investments.products.read');
  $('source-account').disabled=busy;
}
async function api(path, body) {
  const response = await fetch(path, {method:body === undefined ? "GET" : "POST", headers:{Authorization:"Bearer " + token,"Content-Type":"application/json"},body:body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) {const error=new Error(result.message || result.error);error.code=result.error;throw error;}
  return result;
}
function details(id, rows) {
  $(id).replaceChildren();
  for (const [name, value] of rows) {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = name; dd.textContent = String(value); $(id).append(dt, dd);
  }
}
function render(current) {
  task = current;
  auditProof = null;
  $("proof-panel").hidden = !(task.receipt || task.audit_refs?.length);
  $("proof-status").textContent = ""; $("proof-details").replaceChildren(); $("export-proof").hidden = true;
  sessionStorage.setItem("trusta-task", task.task_id);
  $("task-meta").textContent = (labels[task.status] || task.status) + " · " + task.task_id;
  $("steps").replaceChildren();
  task.steps.forEach((step, i) => {
    const li = document.createElement("li"), label = document.createElement("span"), status = document.createElement("span");
    li.dataset.state = step.status; label.textContent = step.step_id + " " + (readStepNames[step.step_id] || names[i] || step.name); status.textContent = labels[step.status];
    li.append(label,status); $("steps").append(li);
  });
  const a = task.proposed_actions[0], auth = task.authorization;
  const receipt=task.receipt || task.reconciliation?.receipt;
  $('reconcile').hidden=!(task.status==='MANUAL_REVIEW' && task.task_type==='TRANSFER' && bank?.provider==='revolut' && task.reconciliation?.status!=='VERIFIED');
  $("auth-panel").hidden = !(task.status === "WAITING_USER" && a && auth);
  if (a && auth) {
    details("auth-details", [["收款人",task.context.recipient.name],["金额",money(a.amount,a.currency)],["付款账户",a.source_account_id],["币种",a.currency],["授权动作",auth.action_hash]]);
    const expired = auth.status === "EXPIRED" || Date.now()/1000 >= auth.expires_at;
    $("strong-label").hidden = auth.auth_method !== "sandbox_secret";
    $("approve").hidden = expired; $("deny").hidden = expired; $("renew").hidden = !expired;
    $("expiry").textContent = expired ? "授权已过期。重新授权会再次检查账户状态；动作过期则需重新提交请求。" : "授权有效至 " + new Date(auth.expires_at*1000).toLocaleTimeString("zh-CN", {timeZone:"Asia/Shanghai"}) + "（北京时间）";
  }
  const readOnly = task.task_type !== "TRANSFER";
  $("result-panel").hidden = !["COMPLETED","MANUAL_REVIEW","FAILED","BLOCKED","CANCELLED"].includes(task.status) || (readOnly && task.status === "COMPLETED");
  $("result-title").textContent = labels[task.status] || "转账结果";
  $("result-description").textContent = task.status === "COMPLETED" ? "转账已结算，银行记录与授权内容一致。" : task.status === "MANUAL_REVIEW" ? "资金可能已变动。请人工核查本笔交易，不要重复发起转账。" : task.status === "CANCELLED" ? "本次待处理请求已取消；已执行交易不会被撤销。" : "本次请求未完成：" + task.errors.join("、");
  if(task.reconciliation?.status==='VERIFIED') {
    $('result-title').textContent='补充核验通过';
    $('result-description').textContent='银行记录显示交易已完成，补充核验通过；原任务保留待核查历史，没有再次付款。';
  }
  details("result-details", receipt ? [["交易编号",receipt.bank_transaction_id],["核验结果",receipt.verification_status],["回执编号",receipt.receipt_id],["审计记录",receipt.audit_ref]] : []);
  $("trace").replaceChildren();
  const traceRows = readOnly ? [["任务输入",task.context.normalized],["计划",task.dag],["读取依据",task.context],["分析结果",task.result],["审计引用",task.audit_refs]] : [["任务输入",task.context.normalized],["计划",task.dag],["转账提案",a],["规则评估",task.policy_decision],["用户授权",auth],["执行记录",task.execution],["银行核验",task.verification],["回执",task.receipt]];
  if(task.reconciliation) traceRows.push(['补充银行核验与回执',task.reconciliation]);
  if (task.llm_call_refs?.length) traceRows.push(["模型辅助引用",task.llm_call_refs]);
  if (task.presentation) traceRows.push(["说明来源",{source:task.presentation.source,memory_refs:task.presentation.memory_refs || [],request_id:task.presentation.request_id,evidence_refs:task.presentation.evidence_refs}]);
  for (const [name, value] of traceRows) {
    const d=document.createElement("details"),s=document.createElement("summary"),p=document.createElement("pre");
    s.textContent=name+(value ? " · 已记录" : " · 待生成"); p.textContent=JSON.stringify(value || null,null,2);d.append(s,p);$("trace").append(d);
  }
  $("notice").textContent = task.clarification || "";
  renderBusiness(task);
}
function renderBusiness(task) {
  const result = task.result;
  $("business-panel").hidden = !result || task.status !== "COMPLETED" || task.task_type === "TRANSFER";
  $("business-summary").replaceChildren(); $("business-results").replaceChildren();
  if (!result || task.task_type === "TRANSFER") return;
  function line(target, title, text) {
    const card=document.createElement("div"),h=document.createElement("h3"),p=document.createElement("p");
    h.textContent=title; p.textContent=text; card.append(h,p);$(target).append(card);
  }
  if (task.presentation) line("business-summary","有据说明",task.presentation.text);
  if (task.task_type === "BILL_ANALYSIS") {
    $("business-title").textContent=result.month+" 消费分析";
    $("business-description").textContent=result.analysis_basis;
    const s=result.summary,comparison=result.monthly_comparison;
    line("business-summary","月度汇总", "消费 "+money(s.gross_spend,s.currency)+" · 退款 "+money(s.refunds,s.currency)+" · 净消费 "+money(s.net_spend,s.currency)+" · 已结算 "+s.settled_count+" 笔");
    line("business-summary","与上月比较", "上月净消费 "+money(comparison.previous_summary.net_spend,s.currency)+" · 本月差额 "+money(comparison.net_change,s.currency)+(comparison.net_change_bps === null ? " · 无有效比例基准" : " · 变化 "+(comparison.net_change_bps/100).toFixed(2)+"%"));
    result.categories.forEach(c=>line("business-results",c.label, "净消费 "+money(c.net_spend,s.currency)+" · 消费占比 "+(c.gross_share_bps/100).toFixed(2)+"%"));
    line("business-results","异常提示 · "+result.anomalies.flags.length+" 笔",result.anomalies.interpretation);
    result.anomalies.flags.forEach(f=>line("business-results",f.merchant+" · "+money(f.amount,s.currency),f.reasons.join("；")+" · 记录 "+f.transaction_id));
  } else {
    $("business-title").textContent="风险等级 R"+result.customer_risk_level+" · 理财推荐";
    $("business-description").textContent=result.message;
    line("business-summary","推荐依据",result.selection_basis+" "+result.limitations);
    result.recommendations.forEach(p=>line("business-results",p.name+" · R"+p.risk_level,p.reasons.join(" ")+" 风险说明："+p.risk_note));
  }
}
async function refreshInfo() {
  bank=await api('/api/bank');
  if(!bank.accounts.some(a=>a.id===sourceAccount)) sourceAccount=bank.accounts.find(a=>!a.frozen)?.id || bank.accounts[0]?.id || '';
  sessionStorage.setItem('trusta-source-account',sourceAccount);
  $('source-account').replaceChildren();
  bank.accounts.forEach(a=>{const option=document.createElement('option');option.value=a.id;option.textContent=a.name+' · '+a.currency+(a.frozen?' · 不可付款':'');$('source-account').append(option);});
  $('source-account').value=sourceAccount;
  const external=bank.environment!=='local';
  $('environment').textContent=external ? (bank.provider==='revolut'?'Revolut':'外部银行')+(bank.environment==='sandbox'?' 官方沙箱 · 测试资金':' · 正式环境') : '本地虚拟银行 · 演示资金';
  if(external && $('request').value==='给张三转 2000 元') $('request').value='分析 '+$('transaction-month').value+' 消费';
  if(task) $('reconcile').hidden=!(task.status==='MANUAL_REVIEW' && task.task_type==='TRANSFER' && bank.provider==='revolut' && task.reconciliation?.status!=='VERIFIED');
  $('bank-notice').textContent=external ? (bank.writes_enabled ? '仅支持白名单内同币种小额转账，每笔需要单独授权。' : '当前只读：可读取账户交易和分析账单。') : '切换账户会取消当前对话中的未确认请求。';
  if(external) $('bank-notice').textContent+=' 切换账户会取消当前对话中的未确认请求。';
  payees=(await api('/api/payees')).payees;
  const [balance, audit, list, memory] = await Promise.all([sourceAccount ? api('/api/balance?account_id='+encodeURIComponent(sourceAccount)) : Promise.resolve(null),api("/api/audit"),api("/api/tasks"),api("/api/memory")]);
  renderMemory(memory.entries);
  $("balance").textContent = balance ? "账户余额 " + money(balance.balance,balance.currency || 'CNY') : '没有可读取账户';
  $("audit").textContent = audit.valid ? "审计哈希链验证通过" : "审计哈希链验证失败";
  $("history").replaceChildren();
  list.tasks.reverse().slice(0,15).forEach(t => {
    const button=document.createElement("button");button.textContent=t.user_goal+" · "+(labels[t.status] || t.status);
    button.addEventListener("click",()=>run(async()=>render(await api("/api/tasks/"+t.task_id))));$("history").append(button);
  });
}
async function run(work) {
  if (busy) return;
  busy=true; document.querySelectorAll("button").forEach(b=>b.disabled=true);applyBankControls(); $("notice").textContent="";
  try {await work(); await refreshInfo();} catch(error) {$("notice").textContent=error.message;} finally {busy=false;document.querySelectorAll("button").forEach(b=>b.disabled=false);applyBankControls();$("strong-secret").value="";}
}
function renderMemory(entries) {
  $("memory-list").replaceChildren();
  const labels={PREFERENCE:"说明偏好",CONVERSATION_REFERENCE:"会话引用",RISK_REFERENCE:"风险档案引用"};
  for(const entry of entries){
    const row=document.createElement("div"),text=document.createElement("p"),button=document.createElement("button");
    text.textContent=(labels[entry.kind] || "保存项")+(entry.kind==="PREFERENCE" ? " · "+({brief:"简短",detailed:"详细",monthly_summary:"月度汇总",anomaly_summary:"异常提示"}[entry.value] || entry.value) : "")+" · 有效至 "+new Date(entry.expires_at*1000).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"});
    button.className="secondary";button.textContent="忘记";button.addEventListener("click",()=>run(async()=>{await api("/api/memory/"+entry.memory_id+"/forget",{});$("memory-notice").textContent="已忘记；已生成的报告仍保留原记录。";}));
    row.append(text,button);$("memory-list").append(row);
  }
  if(!entries.length)$("memory-list").textContent="尚未保存偏好。";
}
$("memory-form").addEventListener("submit",event=>{event.preventDefault();run(async()=>{
  await api("/api/memory/preferences",{key:"explanation_style",value:$("memory-style").value,consent:$("memory-consent").checked,ttl_seconds:604800});
  $("memory-consent").checked=false;$("memory-notice").textContent="已保存 7 天，用于之后生成的说明。";
});});
function renderDialogue(current) {
  $("dialogue").replaceChildren();
  for (const turn of current.turns || []) {
    const user=document.createElement("p");user.dataset.role="user";user.textContent="你："+turn.text;$("dialogue").append(user);
    if(turn.response){const assistant=document.createElement("p");assistant.textContent="TRUSTA："+turn.response.message;$("dialogue").append(assistant);}
  }
  $("dialogue").scrollTop=$("dialogue").scrollHeight;
}
async function loadConversation() {
  const id=sessionStorage.getItem("trusta-conversation");
  if(id){
    conversation=await api("/api/conversations/"+id);
    renderDialogue(conversation);
    if(conversation.status!=="ACTIVE"){conversation=null;sessionStorage.removeItem("trusta-conversation");sessionStorage.removeItem("trusta-pending-turn");}
  }
  const pending=JSON.parse(sessionStorage.getItem("trusta-pending-turn") || "null");if(pending)$("request").value=pending.text;
}
$("request-form").addEventListener("submit",event=>{event.preventDefault();run(async()=>{
  if(!sourceAccount) throw new Error('请先选择可读取账户。');
  if(bank?.environment!=='local' && /转/.test($('request').value)) {
    if(!bank.writes_enabled) throw new Error('当前连接只读，尚未启用沙箱付款。');
    if(conversation) {await api('/api/conversations/'+conversation.conversation_id+'/close',{});conversation=null;sessionStorage.removeItem('trusta-conversation');sessionStorage.removeItem('trusta-pending-turn');}
    render(await api('/api/tasks',{text:$('request').value,source_account_id:sourceAccount}));return;
  }
  if(!conversation){conversation=await api("/api/conversations",{});sessionStorage.setItem("trusta-conversation",conversation.conversation_id);}
  let pending=JSON.parse(sessionStorage.getItem("trusta-pending-turn") || "null");
  if(pending && (pending.conversation_id!==conversation.conversation_id || pending.text!==$("request").value)) throw new Error("上一轮状态尚未确认，请重试原请求或刷新对话。");
  if(pending && pending.source_account_id && pending.source_account_id!==sourceAccount) throw new Error('待处理请求绑定另一账户，请先刷新原请求。');
  if(!pending){pending={conversation_id:conversation.conversation_id,turn_id:crypto.randomUUID(),revision:conversation.revision,text:$("request").value,source_account_id:sourceAccount};sessionStorage.setItem("trusta-pending-turn",JSON.stringify(pending));}
  let reply;
  try {reply=await api("/api/conversations/"+pending.conversation_id+"/turns",{turn_id:pending.turn_id,revision:pending.revision,text:pending.text,source_account_id:pending.source_account_id || sourceAccount});}
  catch(error){if(["INVALID_INPUT","CONVERSATION_STALE","CONVERSATION_EXPIRED","CONVERSATION_ACCESS_DENIED","TURN_ID_REUSED","CONVERSATION_LIMIT"].includes(error.code)){sessionStorage.removeItem("trusta-pending-turn");await loadConversation();}throw error;}
  sessionStorage.removeItem("trusta-pending-turn");
  conversation=await api("/api/conversations/"+pending.conversation_id);renderDialogue(conversation);
  if(reply.task)render(reply.task);else $("auth-panel").hidden=true;
  $("notice").textContent=reply.message;
  $("request").value="";
});});
$("new-conversation").addEventListener("click",()=>run(async()=>{
  if(conversation)await api("/api/conversations/"+conversation.conversation_id+"/close",{});
  sessionStorage.removeItem("trusta-pending-turn");
  conversation=await api("/api/conversations",{});sessionStorage.setItem("trusta-conversation",conversation.conversation_id);
  renderDialogue(conversation);$("auth-panel").hidden=true;$("notice").textContent="已开始新对话，原待处理请求已取消。";
  task=null;sessionStorage.removeItem("trusta-task");$("steps").replaceChildren();$("task-meta").textContent="尚未创建任务";$("result-panel").hidden=true;$("business-panel").hidden=true;$("trace").replaceChildren();$("proof-panel").hidden=true;auditProof=null;
}));
for (const [id,text] of [["example-transfer","给张三转 2000 元"],["example-finance","分析2026-09消费"],["example-wealth","推荐适合我的理财产品"]]) $(id).addEventListener("click",()=>{const currency=bank?.accounts.find(a=>a.id===sourceAccount)?.currency;const payee=payees.find(p=>p.currency===currency);$("request").value=bank?.environment!=='local' ? (id==='example-transfer' ? '从 '+sourceAccount+' 给 '+payee.name+' 转 0.01 '+currency : id==='example-finance' ? '分析'+$('transaction-month').value+'消费' : text) : text;});
$('source-account').addEventListener('change',()=>{const selected=$('source-account').value;run(async()=>{if(conversation)await api('/api/conversations/'+conversation.conversation_id+'/close',{});conversation=null;['trusta-conversation','trusta-pending-turn','trusta-task'].forEach(k=>sessionStorage.removeItem(k));sourceAccount=selected;sessionStorage.setItem('trusta-source-account',selected);task=null;$('auth-panel').hidden=true;$('business-panel').hidden=true;$('result-panel').hidden=true;$('steps').replaceChildren();$('dialogue').replaceChildren();$('transaction-records').replaceChildren();$('task-meta').textContent='尚未创建任务';$('trace').replaceChildren();$('proof-panel').hidden=true;auditProof=null;});});
$('transaction-month').value=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit'}).format(new Date());
$('load-transactions').addEventListener('click',()=>run(async()=>{
  const result=await api('/api/transactions?account_id='+encodeURIComponent(sourceAccount)+'&month='+encodeURIComponent($('transaction-month').value));
  $('transaction-records').replaceChildren();
  result.records.forEach(r=>{const p=document.createElement('p');p.textContent=new Date(r.occurred_at*1000).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'})+' · '+(r.merchant || r.bank_type || '交易')+' · '+(r.direction==='IN'?'入账 ':'')+money(r.amount,r.currency)+' · '+r.status;$('transaction-records').append(p);});
  $('transaction-note').textContent=result.records.length+' 条记录；月份按北京时间'+(bank?.environment==='local'?'发生时间':'创建时间')+'筛选。充值和转账不计入消费。';
}));
for (const [id,approve] of [["approve",true],["deny",false]]) $(id).addEventListener("click",()=>run(async()=>render(await api("/api/tasks/"+task.task_id+"/authorize",{action_hash:task.authorization.action_hash,approve,strong_secret:$("strong-secret").value}))));
$("renew").addEventListener("click",()=>run(async()=>render(await api("/api/tasks/"+task.task_id+"/renew",{}))));
$("refresh").addEventListener("click",()=>run(async()=>{if(task)render(await api("/api/tasks/"+task.task_id));}));
$('reconcile').addEventListener('click',()=>run(async()=>render(await api('/api/tasks/'+task.task_id+'/reconcile',{}))));
$("verify-proof").addEventListener("click",()=>run(async()=>{
  const selectedTaskId=task.task_id;
  const receipt=task.receipt || task.reconciliation?.receipt;
  const query=receipt ? "receipt_id="+encodeURIComponent(receipt.receipt_id) : "audit_event_id="+encodeURIComponent(task.audit_refs[0]);
  try {
    const proof=await api("/api/audit/proof?"+query);
    if(task?.task_id!==selectedTaskId)return;
    auditProof=proof;
    $("proof-status").textContent=proof.record_verified ? "记录、检查点签名与包含证明已由服务核验通过。" : "证明核验失败。";
    const anchor=proof.anchor;
    const anchorLabel=proof.anchor_verified && anchor?.adapter==="LOCAL_DEVELOPMENT" ? "本地开发记录已核对（非区块链）" : "尚无已核验锚定";
    details("proof-details",[["审计记录",proof.audit_event_id],["记录摘要",proof.event_hash],["冻结批次",proof.checkpoint.checkpoint_id],["批次记录数",proof.checkpoint.leaf_count],["Merkle 根",proof.merkle_root],["签名密钥状态",proof.signer_key_status],["锚定",anchorLabel]]);
    $("export-proof").hidden=false;
  } catch(error) {
    $("proof-status").textContent=error.code==="AUDIT_CHECKPOINT_PENDING" ? "银行结果已完成；审计批次待后台冻结，稍后可再次验证。" : error.code==="PHASE4_DISABLED" ? "当前使用 V2 兼容配置，尚未启用单条证明。" : error.message;
  }
}));
$("export-proof").addEventListener("click",()=>{
  if(!auditProof)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(auditProof,null,2)],{type:"application/json"}));
  const link=document.createElement("a");link.href=url;link.download="trusta-audit-proof.json";link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
});
run(async()=>{
  if(!token)throw new Error("请通过启动终端中显示的完整链接打开页面。");
  const last=sessionStorage.getItem("trusta-task");if(last)render(await api("/api/tasks/"+last));
  await loadConversation();
});
