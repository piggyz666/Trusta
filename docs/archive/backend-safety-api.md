# 后端安全 API（第一批）

Competition 模式在原可信金融执行链内运行。历史 compatibility 模式保留演示口令，不提供生产 MFA 保证。

## 配置

`TRUSTA_SAFETY_PROFILE=competition` 为本地启动默认；人民币日累计确认额度 `TRUSTA_DAILY_TRANSFER_CENTS=100000`，单位为分。5次认证失败、3次写异常的默认阈值分别可由 `TRUSTA_AUTH_FAILURE_LIMIT`、`TRUSTA_EXECUTION_FAILURE_LIMIT` 配置。

`TRUSTA_SECURITY_OPERATOR_TOKEN` 为独立操作员秘密，不能复用用户访问令牌、银行 token 或模型 key，不能交给 Agent/LLM；未配置时操作员解锁/备注不可调用。所有新接口沿用服务端固定身份、Host/Origin 校验与 Bearer 认证。

## 用户接口

- `GET /api/security/status`：自身安全配置、计数、锁定和绑定状态，不返回种子、验证码或证明密钥。
- `POST /api/security/totp/enroll`，JSON `{}`：只在未绑定时发起绑定，返回一次性种子与 otpauth URI。可信本地用户用自己的认证器导入；不要公开返回值。待绑定不能反复覆盖以重置期限。
- `POST /api/security/totp/confirm`，JSON `{"code":"认证器的六位码"}`：在期限内确认绑定。该码被消费，下一个高风险操作使用后续时间步的码。
- 原金融授权接口保持 `action_hash`、`approve`、`strong_secret` 字段；competition 时 `strong_secret` 接收认证器验证码，而不是演示口令。确认与 proof 都绑定原 authorization_id 与精确动作。
- `POST /api/tasks/{task_id}/review`，JSON `{"note":"需要核查的具体原因"}`：只允许自身 MANUAL_REVIEW 任务，重复请求返回同一案件。
- `GET /api/security/reviews`：自身案件。

普通用户不能直接替换已绑定认证器，当前没有生产级 MFA 恢复/轮换流程。TOTP 基于 [RFC 6238](https://www.rfc-editor.org/rfc/rfc6238) 和 [RFC 4226](https://www.rfc-editor.org/rfc/rfc4226)，具有已知算法向量测试；不是银行的 SCA 认证或抗钓鱼证明。

## 操作员接口

以下均同时需要普通用户身份与 `X-TRUSTA-Operator` 独立凭据。只处理当前用户范围，操作员不能提交替代金融审批：

- `POST /api/security/unlock`，JSON `{"reason":"经过检查后的解锁理由"}`：清失败计数并解除金融锁，审计记录。不会释放未知交易额度、撤销支付或补发支付。
- `POST /api/tasks/{task_id}/review-note`，JSON `{"note":"核查结果及依据"}`：案件标记 ACKNOWLEDGED，任务金融状态不变。现有 bank reconciliation 才能基于真实读回产生补充核验/回执。

## 额度与边界

人民币 <=1000 元的日累计转账仍逐笔确认，超过1000元强验证；外币不换算成人民币，始终强验证。按用户和上海日期累计，范围仅 TRUSTA 发起，不覆盖外部银行其他客户端产生的所有转账。

执行前并发安全登记防止同时审批两笔绕过额度。明确失败与未知结果暂不自动释放已登记额度，采用保守当日累计；没有自动银行写重试。读取操作保留原只读 Capability 检查与审计。

独立核验若明确返回 AUTHORITATIVE_STATE_MISMATCH，会立即持久化金融锁。未知状态、读取超时及 Finance 异常候选不能冒充已证实的欺诈事实，不触发该分支。

后端允许历史隔离 Config 使用 legacy；competition 数据库禁止静默降级。密钥丢失/签封损坏报错；这些本地控制不等同于生产身份、KMS/HSM或受监管银行上线验收。
