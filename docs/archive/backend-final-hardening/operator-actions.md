# 集中操作员事项（尚未签名/广播）

已自动完成：Infura 保留；本地 .env 已接入通过核对的 Pocket Network HTTPS 第二 RPC；Chain ID 11155111；余额 0.0103 SepoliaETH；nonce 0；部署实时 estimateGas 3,802,284，首次 commit state-override estimateGas 356,357。准备期样本不是 TRUSTA Anchor 交易，实际 Anchor 验证仍待部署。

公开部署钱包/Publisher `0xF84C2E84F43460A94bb24fFEb6d4a61B75De86bE`；source SHA-256 `c18ab76d2f695f6c119aa6ad78ee574067e4524baef8cf2c166b18e3f0b580dc`；creation SHA-256 `c7c2ab864bed3a2cb49a72e6ca702adc0d356899849444f2d6d39f573d63da17`；预期 runtime Keccak `0x8e36d0fcd7489c6f9a9352cbfbd01109f2d1f7d2bb8b8214cb5ebb02bc63efe9`。现有受控本地加密 Keystore 流程与地址绑定检查已测试；MetaMask 公共地址不构成签名权限。

1. 本人本机运行 `python -m scripts.anchor_operator enroll-keystore`，隐藏输入现有测试钱包私钥及 12 字符以上密码，生成 data/anchor-wallet.json。若已有兼容加密文件则复用。.env 只填写 ANCHOR_KEYSTORE_PATH；密码仅本人进程环境 ANCHOR_KEYSTORE_PASSWORD，不放 .env、聊天、命令行参数或日志。不需要再注册 RPC、不创建新钱包。
2. 签名器配置完成后重新只读准备部署计划，核对 chain/sender/nonce/code/gas/费用；明确批准新鲜 exact-plan hash。当前报告 hash `0450d815c4d1d260ce996b2e23124067b0580405cf5b752ff014dd27addda617` 的 TTL 仅 120 秒，交付时已过期，不可直接执行。签名和广播开关保持 false，直到具体计划明确批准。
3. 部署确认后再从真实业务 Audit Log/checkpoint 由现有 Outbox/Worker 准备 commit，独立批准该计划；部署授权不能沿用。采用原独立验证器检查至少 3 confirmations、canonical/reorg、实际 receipt/event、code/Publisher/root read-back，两运营商通过才生成 ExternalAnchorProof。
4. 若希望继续新实时 LLM 评测，需要明确新的模型请求次数与费用预算。旧 800 次已耗尽；本輪没有擅自继续调用。新数据集与 16 个未运行 holdout 已准备；独立作者 gold 仍有价值。

本次费用报价上界：deploy 4.56420107712E-7 + commit 4.2776584128E-8 = 4.9919669184E-7 ETH；报价后余额至少 0.01029950080330816。margin gasLimit 4,562,741 / 427,629，maxFee 0.000100032 Gwei、tip 0.000100002 Gwei。实际链上 gasUsed 尚不存在，不拿上限当消耗。

费用以 unsigned_deployment_plan 的现有受保护准备器报价为准；内嵌 read_only_estimation.current_eip1559 是另一个保守费用观察值，并非本次交易计划。两者不能混用。低报价不保证确认时延，后续按新鲜 RPC 与既有硬上限重新准备；不为追求速度自动提价。

硬保护有效最高两笔费用 .0097 ETH + .0002 留存 = .0099 ETH，现有 .0103 足够；名义更保守两笔 cost caps .0100 + 留存 .0002 也有余额。上涨到当前 gasLimit × 2 Gwei 时部署超过 .0085 cost cap 必须拒绝，不能无限提价。具体交易按新鲜实时估算执行；commit 当前只是新合约 state override，不是已部署合约的真实消费。

状态严格分为 PREPARED→APPROVED→SIGNED→BROADCAST→CONFIRMED→VERIFIED。本轮只到 PREPARED，real_wallet_signatures=0 / broadcasts=0 / ETH spent=0；不生成假的部署/commit hash 或外部成功证明。
