import {PersonalProfile} from "../components/PersonalLife.jsx";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { api, auth, explain } from "../api.js";
import { Tag, useToast } from "../components/ui.jsx";
import { bankName, dateTime, yuan } from "../format.js";

const TABS = [["account", "账户"], ["model", "模型与接口"], ["access", "访问与设备"], ["feishu", "飞书"], ["security", "安全"],
  ["rules", "联动规则"], ["privacy", "隐私与审计"], ["appearance", "外观"], ["about", "关于"]];
const PROBE = { VERIFIED: ["green", "已验证"], CONFIGURED: ["amber", "已配置，未检测"], NOT_CONFIGURED: ["neutral", "未配置"], FAILED: ["red", "检测失败"],
  NO_TOOL_CALLING: ["amber", "不支持工具调用"], CIRCUIT_OPEN: ["amber", "暂时熔断"], OK: ["green", "正常"] };

function Row({ label, hint, children }) {
  return <div className="setting-row"><div className="label"><b>{label}</b>{hint && <small>{hint}</small>}</div>{children}</div>;
}
function useQr(text) {
  const [src, setSrc] = useState(null);
  useEffect(() => { if (!text) { setSrc(null); return; } QRCode.toDataURL(text, { margin: 1, width: 352 }).then(setSrc).catch(() => setSrc(null)); }, [text]);
  return src;
}
const LocalOnly = () => <small className="faint" style={{ display: "block", marginTop: 4 }}>为安全起见，只能在运行 TRUSTA 的电脑上修改</small>;

const settingsCache = { data: null };

export default function Settings({ capabilities, refresh, tab, setTab, onSignOut, devMode, setDevMode }) {
  const toast = useToast();
  const [data, setData] = useState(settingsCache.data);
  const [error, setError] = useState(null);
  const current = tab || "account";
  const load = () => api.settings().then((d) => { settingsCache.data = d; setData(d); }).catch((e) => setError(explain(e)));
  useEffect(() => { load(); }, []);

  if (!data) return <div className="page"><div className="inner">{error ? <div className="empty">{error}</div> : <span className="muted">读取中…</span>}</div></div>;
  const props = { data, reload: load, refresh, toast, capabilities };

  return (
    <div className="page">
      <div className="inner">
        <header className="page-head"><h1>设置</h1>
          <p>{data.editable ? "你正在运行 TRUSTA 的电脑上，可以修改全部设置。" : "你在配对设备上：可以查看设置；模型、密钥和访问方式只能在电脑上修改。"}</p></header>
        <div className="settings">
          <nav className="settings-nav" aria-label="设置分类">
            {TABS.map(([key, label]) => <button key={key} aria-current={current === key} onClick={() => setTab(key)}>{label}</button>)}
          </nav>
          <div className="panel" style={{ padding: "4px 20px" }}>
            {current === "account" && <><Account {...props} onSignOut={onSignOut} /><PersonalProfile/></>}
            {current === "model" && <Model {...props} />}
            {current === "access" && <Access {...props} />}
            {current === "feishu" && <Feishu {...props} />}
            {current === "security" && <Security {...props} />}
            {current === "rules" && <Rules {...props} />}
            {current === "privacy" && <Privacy {...props} />}
            {current === "appearance" && <Appearance devMode={devMode} setDevMode={setDevMode} />}
            {current === "about" && <About {...props} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Account({ data, onSignOut }) {
  return (
    <>
      <Row label="客户编号" hint="TRUSTA 以这个身份调用银行接口"><span className="mono">{data.account.owner}</span></Row>
      {data.account.accounts.map((a) => <Row key={a.id} label={a.name} hint={a.id === data.account.default_account_id ? "默认付款账户" : null}><span className="mono">{a.id}</span></Row>)}
      <Row label="这台设备" hint={auth.isDevice() ? "已配对的设备，凭据保存在本浏览器" : "本机访问，凭据只在这个标签页内有效"}>
        <button className="btn small" onClick={onSignOut}>退出登录</button>
      </Row>
    </>
  );
}

function Model({ data, reload, refresh, toast }) {
  const [model, setModel] = useState(data.llm.model || data.llm.choices[0]);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(null);
  const [probes, setProbes] = useState({});
  const state = probes.llm?.status || data.llm.state;
  const [tone, label] = PROBE[state] || ["neutral", state];

  async function probe() {
    setBusy("probe");
    try { const r = await api.probe(); setProbes(r.probes); await refresh(); await reload(); toast("检测完成"); }
    catch (e) { toast(explain(e), "error"); } finally { setBusy(null); }
  }
  async function save() {
    setBusy("save");
    try {
      const body = {};
      if (model !== data.llm.model) body.model = model;
      if (key) body.api_key = key;
      const r = await api.updateLlm(body);
      setKey(""); setProbes({ llm: r }); await refresh(); await reload();
      toast(r.status === "VERIFIED" ? "已切换并验证通过" : "已保存，但检测未通过：" + (r.code || r.status), r.status === "VERIFIED" ? "info" : "error");
    } catch (e) { toast(explain(e), "error"); } finally { setBusy(null); }
  }
  const usage = data.llm.usage_today;
  return (
    <>
      <Row label="大模型" hint={data.llm.endpoint || "未接入：使用规则引擎，只认标准说法"}><Tag tone={tone}>{label}</Tag></Row>
      <Row label="模型" hint="需要支持工具调用（function calling）">
        <select className="input" style={{ width: 200 }} value={model} disabled={!data.editable} onChange={(e) => setModel(e.target.value)}>
          {[...new Set([...(data.llm.model ? [data.llm.model] : []), ...data.llm.choices])].map((m) => <option key={m}>{m}</option>)}
        </select>
      </Row>
      <Row label="API Key" hint={<>当前：{data.llm.key || "未设置"}；只保存在本机 .env 中，不会显示完整内容{!data.editable && <LocalOnly />}</>}>
        <input className="input" type="password" autoComplete="off" style={{ width: 220 }} placeholder="粘贴新的 Key" value={key} disabled={!data.editable} onChange={(e) => setKey(e.target.value.trim())} />
      </Row>
      {data.editable && <Row label="" ><button className="btn primary small" disabled={busy || (!key && model === data.llm.model)} onClick={save}>{busy === "save" ? "保存并检测中…" : "保存并检测"}</button></Row>}
      <Row label="今日调用" hint={`每个账户每天最多 ${data.llm.daily_call_limit} 次，超出后自动改用规则引擎`}>
        <span className="num">{usage?.calls ?? 0} 次</span></Row>
      <Row label="银行接口" hint={`${data.bank.tools.length} 个银行能力${data.bank.config_file ? " · 来自银行配置文件" : ""}`}>
        <span>{bankName(data.bank)}</span></Row>
      <Row label="连通性检测" hint="依次检测大模型、银行接口、飞书；检测结果决定功能页里哪些功能亮起">
        <button className="btn small" disabled={!!busy} onClick={probe}>{busy === "probe" ? "检测中…" : "重新检测"}</button></Row>
      {Object.keys(probes).length > 0 && (
        <div style={{ padding: "8px 0 14px", display: "flex", gap: 8, flexWrap: "wrap" }}>
          {Object.entries(probes).map(([k, v]) => { const [t, l] = PROBE[v.status] || ["neutral", v.status];
            return <Tag key={k} tone={t}>{{ llm: "大模型", bank: "银行", feishu: "飞书" }[k]}：{l}{v.latency_ms ? ` · ${v.latency_ms}ms` : ""}</Tag>; })}
        </div>
      )}
    </>
  );
}

function Access({ data, reload, toast }) {
  const [pair, setPair] = useState(null);
  const [mode, setMode] = useState(data.access.mode);
  const [host, setHost] = useState(data.access.public_host || "");
  const qr = useQr(pair?.pair_url);
  const urls = data.access.urls;

  async function start() {
    try { setPair(await api.startPairing("device")); } catch (e) { toast(explain(e), "error"); }
  }
  async function saveMode() {
    try { await api.updateAccess(mode === "tunnel" ? { mode, public_host: host } : { mode }); toast("已保存，重启 TRUSTA 后生效"); }
    catch (e) { toast(explain(e), "error"); }
  }
  async function revoke(id) {
    try { await api.revokeDevice(id); toast("已撤销，这台设备需要重新配对"); reload(); } catch (e) { toast(explain(e), "error"); }
  }
  return (
    <>
      <Row label="访问方式" hint={<>本机：只有这台电脑能打开；局域网：同一 Wi-Fi 下手机扫码；隧道：通过你自己的域名从外网访问{!data.editable && <LocalOnly />}</>}>
        <div className="segmented">
          {[["local", "本机"], ["lan", "局域网"], ["tunnel", "隧道"]].map(([k, l]) =>
            <button key={k} aria-pressed={mode === k} disabled={!data.editable} onClick={() => setMode(k)}>{l}</button>)}
        </div>
      </Row>
      {mode === "tunnel" && <Row label="公网域名" hint="例如 bank.example.com，需要你自己的隧道（如 Cloudflare Tunnel）指向本机">
        <input className="input" style={{ width: 220 }} value={host} disabled={!data.editable} onChange={(e) => setHost(e.target.value.trim())} /></Row>}
      {data.editable && (mode !== data.access.mode || (mode === "tunnel" && host !== data.access.public_host)) &&
        <Row label=""><button className="btn primary small" onClick={saveMode}>保存（需重启）</button></Row>}
      <Row label="访问地址">
        <div style={{ display: "grid", gap: 2, textAlign: "right" }}>{Object.entries(urls).map(([k, u]) => <span key={k} className="mono">{u}</span>)}</div></Row>
      <Row label="连接手机" hint={data.access.mode === "local" ? "先把访问方式改为局域网或隧道并重启" : "用手机相机扫码，或在手机浏览器打开地址后输入配对码，5 分钟内有效"}>
        <button className="btn small" disabled={data.access.mode === "local" || !data.editable} onClick={start}>生成配对码</button></Row>
      {pair && (
        <div style={{ display: "flex", gap: 22, alignItems: "center", padding: "6px 0 16px", flexWrap: "wrap" }}>
          {qr && <img className="qr" src={qr} alt="配对二维码" />}
          <div style={{ display: "grid", gap: 6 }}>
            <span className="stat-label">配对码</span><span className="code-big num">{pair.code}</span>
            <span className="faint" style={{ fontSize: 13 }}>{dateTime(pair.expires_at)} 前有效，只能用一次</span>
          </div>
        </div>
      )}
      <div style={{ padding: "12px 0" }}>
        <b style={{ fontWeight: 560 }}>已配对设备</b>
        {data.access.devices.length === 0 && <div className="muted" style={{ fontSize: 14, marginTop: 6 }}>还没有配对的设备。</div>}
        {data.access.devices.map((d) => (
          <div key={d.id} className="kv" style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
            <span>{d.name} <small className="faint">配对于 {dateTime(d.created_at)}{d.last_seen ? ` · 最近 ${dateTime(d.last_seen)}` : ""}</small></span>
            {d.revoked ? <Tag tone="neutral">已撤销</Tag> : <button className="btn small danger" onClick={() => revoke(d.id)}>撤销</button>}
          </div>
        ))}
      </div>
    </>
  );
}

function Feishu({ data, reload, toast }) {
  const [code, setCode] = useState(null);
  const f = data.feishu;
  async function bind() { try { setCode(await api.startPairing("feishu")); } catch (e) { toast(explain(e), "error"); } }
  async function unbind() { try { await api.unbindFeishu(); toast("已解除飞书绑定"); reload(); } catch (e) { toast(explain(e), "error"); } }
  return (
    <>
      <Row label="飞书机器人" hint={f.configured ? `应用 ${f.app_id} · ${f.mode === "longconn" ? "长连接（无需公网）" : "事件回调"}` : "未配置：运行 python -m scripts.setup 填写飞书应用凭据"}>
        <Tag tone={f.available ? "green" : f.configured ? "amber" : "neutral"}>{f.available ? "可用" : f.configured ? "已配置，未连通" : "未配置"}</Tag></Row>
      <Row label="账户绑定" hint="绑定后可以在飞书里查账、转账；黄色操作可在飞书确认，红色操作会引导回 APP 验证">
        {f.bound ? <button className="btn small danger" onClick={unbind}>解除绑定</button>
          : <button className="btn small" disabled={!f.available || !data.editable} onClick={bind}>生成绑定码</button>}
      </Row>
      {code && <div style={{ padding: "4px 0 16px", display: "grid", gap: 6 }}>
        <span className="code-big num">{code.code}</span><span className="muted">{code.instruction}，5 分钟内有效</span></div>}
    </>
  );
}

function Security({ data, reload, toast }) {
  const sec = data.security;
  const [enroll, setEnroll] = useState(null);
  const [otp, setOtp] = useState("");
  const [hours, setHours] = useState(24);
  const qr = useQr(enroll?.otpauth_uri);
  async function start() { try { setEnroll(await api.enrollTotp()); } catch (e) { toast(explain(e), "error"); } }
  async function confirm() {
    try { await api.confirmTotp(otp); setEnroll(null); setOtp(""); toast("验证器已绑定，红色操作可以用它验证"); reload(); } catch (e) { toast(explain(e), "error"); }
  }
  async function protect() {
    try { const r = await api.protectAccount(hours); toast(`转账保护已开启，到 ${dateTime(r.until)}`); reload(); } catch (e) { toast(explain(e), "error"); }
  }
  return (
    <>
      <Row label="权限分级" hint="绿色：只读，直接执行；黄色：涉及资金或卡片，需要你在卡片上确认；红色：大额、新收款人、挂失等，需要验证码">
        <span style={{ display: "flex", gap: 6 }}><Tag tone="green">绿</Tag><Tag tone="amber">黄</Tag><Tag tone="red">红</Tag></span></Row>
      <Row label="安全档位" hint={sec.profile === "competition" ? "强验证已开启" : "基础档：建议开启强验证"}><Tag tone={sec.profile === "competition" ? "green" : "amber"}>{sec.profile}</Tag></Row>
      <Row label="每日转账限额" hint="按北京时间自然日累计，只统计经 TRUSTA 发起的转账"><span className="num">{yuan(sec.daily_limit_minor)}</span></Row>
      <Row label="验证器（TOTP）" hint={sec.sms_demo ? "演示模式下也可用短信验证码（验证码直接显示在卡片上）" : "用手机验证器 App 生成 6 位动态码"}>
        {sec.totp_enrolled ? <Tag tone="green">已绑定</Tag> : <button className="btn small" disabled={sec.profile !== "competition"} onClick={start}>绑定验证器</button>}
      </Row>
      {enroll && (
        <div style={{ display: "flex", gap: 20, padding: "6px 0 16px", flexWrap: "wrap", alignItems: "center" }}>
          {qr && <img className="qr" src={qr} alt="验证器二维码" />}
          <div style={{ display: "grid", gap: 8 }}>
            <span className="muted">用验证器 App 扫码，然后输入显示的 6 位数字</span>
            <input className="input" inputMode="numeric" maxLength={6} value={otp} onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))} style={{ width: 160 }} />
            <button className="btn primary small" disabled={otp.length !== 6} onClick={confirm}>完成绑定</button>
          </div>
        </div>
      )}
      <Row label="转账保护（冷静期）" hint={sec.protection ? `已开启，到 ${dateTime(sec.protection.until)}。期间只能由银行安全人员核实身份后提前解除。`
        : "怀疑被诈骗或被人催促转账时开启：期间暂停转账、新增收款人、心愿金下单和申购理财"}>
        {sec.protection ? <Tag tone="amber">保护中</Tag> : (
          <span style={{ display: "flex", gap: 6 }}>
            <select className="input" style={{ width: 96 }} value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {[1, 24, 48, 72].map((h) => <option key={h} value={h}>{h} 小时</option>)}</select>
            <button className="btn small" onClick={protect}>立即开启</button>
          </span>)}
      </Row>
      <Row label="账户锁定" hint="连续验证失败会临时锁定强验证"><Tag tone={sec.locked ? "red" : "green"}>{sec.locked ? "已锁定" : "正常"}</Tag></Row>
    </>
  );
}

function Rules({ toast }) {
  const [rules, setRules] = useState(null);
  const load = () => api.overview().then((o) => setRules(o.rules)).catch((e) => toast(explain(e), "error"));
  useEffect(() => { load(); }, []);
  async function cancel(id) { try { await api.cancelRule(id); toast("规则已停用"); load(); } catch (e) { toast(explain(e), "error"); } }
  if (!rules) return <div className="muted" style={{ padding: 14 }}>读取中…</div>;
  return (
    <>
      {rules.length === 0 && <div className="empty">没有生效的联动规则。在对话里说“外卖每月超过800就提醒我”即可创建。</div>}
      {rules.map((r) => (
        <Row key={r.id} label={r.name} hint={`${r.trigger} · 已触发 ${r.runs} 次`}><button className="btn small danger" onClick={() => cancel(r.id)}>停用</button></Row>
      ))}
      <div className="faint" style={{ fontSize: 13, padding: "12px 0" }}>规则只在你确认后生效；涉及转账的规则每次触发仍会生成确认卡，只有冻结卡、关闭交易开关这类“收紧”动作可以预先授权自动执行。</div>
    </>
  );
}

function Privacy({ data }) {
  const p = data.privacy;
  return (
    <>
      <Row label="大模型能看到" hint="用于理解你的意思"><span style={{ maxWidth: 280, textAlign: "right" }}>{p.model_sees}</span></Row>
      <Row label="大模型看不到" hint="这些数据只在本机和银行之间流转"><span style={{ maxWidth: 280, textAlign: "right" }}>{p.model_never_sees}</span></Row>
      <Row label="审计哈希链" hint="每一次授权、执行、配置变更都写入链中，可离线验证"><Tag tone={p.audit_chain_valid ? "green" : "red"}>{p.audit_chain_valid ? "完整" : "校验失败"}</Tag></Row>
      <Row label="外部文本隔离" hint="转账备注、商户名等第三方文本先经过注入检测，可疑内容不会进入大模型"><Tag tone="green">始终开启</Tag></Row>
    </>
  );
}

function Appearance({ devMode, setDevMode }) {
  const read = () => { try { return localStorage.getItem("trusta-theme") || "system"; } catch { return "system"; } };
  const [theme, setTheme] = useState(read);
  function apply(value) {
    setTheme(value);
    try { value === "system" ? localStorage.removeItem("trusta-theme") : localStorage.setItem("trusta-theme", value); } catch { /* ignore */ }
    window.__trustaSyncTheme?.();
  }
  const [motion, setMotion] = useState(() => { try { return localStorage.getItem("trusta-motion") !== "off"; } catch { return true; } });
  function flipMotion() {
    const next = !motion; setMotion(next);
    try { localStorage.setItem("trusta-motion", next ? "on" : "off"); } catch { /* ignore */ }
    window.dispatchEvent(new Event("trusta-motion"));
  }
  function flipDev() {
    const next = !devMode; setDevMode(next);
    try { localStorage.setItem("trusta-dev", next ? "1" : "0"); } catch { /* ignore */ }
  }
  return (
    <>
      <Row label="主题" hint="深色为默认">
        <div className="segmented">
          {[["dark", "深色"], ["light", "浅色"], ["system", "跟随系统"]].map(([k, l]) => <button key={k} aria-pressed={theme === k} onClick={() => apply(k)}>{l}</button>)}
        </div>
      </Row>
      <Row label="动态背景" hint="关闭后显示静态画面，更省电"><button role="switch" aria-checked={motion} aria-label="动态背景" className="switch" onClick={flipMotion} /></Row>
      <Row label="开发者模式" hint="在每条回复下显示“查看决策过程”：安全检查、Agent 间签名消息、授权与审计。演示和排查问题时使用">
        <button role="switch" aria-checked={!!devMode} aria-label="开发者模式" className="switch" onClick={flipDev} /></Row>
    </>
  );
}

function About({ data }) {
  return (
    <>
      <Row label="TRUSTA" hint="可信多智能体银行助手 · 六 Agent 协作 · 签名证据链"><span>v1.0</span></Row>
      <Row label="能力快照" hint="当前模型、银行、渠道配置的哈希，写入每个请求的签名来源"><span className="mono">{data.about.snapshot_hash.slice(0, 16)}…</span></Row>
      <Row label="运行模式"><Tag tone={data.about.demo_mode ? "amber" : "green"}>{data.about.demo_mode ? "演示模式" : "正式"}</Tag></Row>
      <Row label="旧版界面" hint="调试用的单页界面"><a href={import.meta.env.BASE_URL + 'legacy'}>打开</a></Row>
    </>
  );
}
