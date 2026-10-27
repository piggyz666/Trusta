import { LIFE_SECTIONS, safeLink } from "../life.js";

export default function Life({ ask, navigate }) {
  return (
    <div className="page">
      <div className="inner">
        <header className="page-head">
          <h1>生活服务</h1>
          <p>机票、火车票、酒店、演出和缴费都在对方官方平台办理。直接在对话里说需求，我会把城市、日期带进链接，点开就是查询结果；本行不参与交易，也不会代你付款。</p>
        </header>
        <div className="life-grid">
          {LIFE_SECTIONS.map((s) => (
            <section key={s.key} className="panel life-section">
              <h2>{s.title}</h2>
              <div className="life-links">
                {s.links.filter(([, url]) => safeLink(url)).map(([label, url]) => (
                  <a key={url} className="btn small" href={url} target="_blank" rel="noopener noreferrer">{label} ↗</a>
                ))}
              </div>
              <div className="life-asks">
                <small className="faint">在对话里试试</small>
                {s.ask.map((q) => <button key={q} className="chip" onClick={() => ask(q)}>{q}</button>)}
              </div>
            </section>
          ))}
        </div>
        <p className="faint rule-note">以上均为第三方网站，价格、余票和退改规则以对方页面为准。TRUSTA 只根据你的原话拼出查询链接，不读取也不保存你在对方网站上的账号信息。</p>
      </div>
    </div>
  );
}
