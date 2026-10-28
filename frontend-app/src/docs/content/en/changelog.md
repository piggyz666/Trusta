# Changelog

## 2026-10-28

- Feishu: the App ID / App Secret can be entered in **Settings → Feishu** on the web, and are checked with Feishu as soon as they are saved; the docs, the setup wizard and the settings page list the scopes the app must have (`im:message.p2p_msg:readonly`, `im:message:send_as_bot`) and every step on the open platform.
- Viewing subscriptions and direct debits is Yellow, as the brief requires: the query is shown and confirmed first, then not asked again for 10 minutes; tests check every tier against the brief.
- Red operations can be approved with face / fingerprint / security key (WebAuthn), bound to that one operation; the start URL is now `http://localhost:8000`.
- Real-bank readiness: pages and chat read only through the bank interface; deposits, FX, gold, funds, loans and the credit account run as bank quote → confirmation → signed execution; incoming payments can come from any bank's transaction list.
- Bank integration kit: data contract with JSON Schemas, adapter template, conformance checker `python -m scripts.bank_check`, security brief for bank review.
- Technical document (architecture, core algorithms, security design); tests that need unpublished material are skipped with the reason.
- Wealth page with gold and FX reference quotes, a converter and a spending-based plan; Bills & subscriptions page; everyday-service links filled from your words.
- Membership points removed; the first conversation no longer opens with a message.

## 2026-10-25

- Landing page: ink-fluid hero, product demo, trust layers, measured results and quick start; Chinese and English.
- Documentation site, including an Integrations page.
- Figures 1 and 2 redrawn (Chinese and English) to tell problem → full chain → measured results.
- Landing page: plain-language measured results, integrations section, light/dark switch; the demo now plays itself.
- One-line installers (Windows / macOS / Linux), `start.sh`, double-click `TRUSTA.cmd`.
- Windows portable package and automated build.
- Fix: `requirements.txt` now lists everything the server needs at start-up, so clean installs no longer fail.

## 2026-10-24

- Web and phone app: chat home, interactive cards, overview and records, feature switches, settings.
- Interactive water background, liquid-glass interface, new icon; light and dark follow the system.
- The decision trace moved behind developer mode.
- Feishu links for red operations open on phones too; conversations are named by what they did.
- README: figures for trusted multi-agent communication and cross-institution collaboration.

## 2026-10-23

- Access modes: this computer only / local network / tunnel; one-time phone pairing with device tokens.
- Feishu persistent-connection worker and binding codes.
- Setup wizard: validates pasted secrets; probe results show details.
- Chat text can never stand in for a confirmation card; Feishu rule-confirmation buttons.

## 2026-10-22

- Capability registry and setup wizard; automation rules engine; trip manager.
- Untrusted-text quarantine; anti-fraud cooling-off; money operations run one at a time.
- Six-scenario closed-loop audit.

## 2026-10-21

- Unified chat API and card protocol.
- Signed multi-agent messaging and the terminal client.
