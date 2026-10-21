"""Compose delivery from real recorded results, refusing a failed regression."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
def read(name):return json.loads((ROOT/name).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(name,value):(ROOT/'docs'/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
    baseline=read('data/hardening-baseline-result.json');final=read('data/hardening-final-result.json')
    if not final['passed'] or final['tests']!=640:raise SystemExit('Final passing full regression is required')
    old=ROOT/'docs/backend-hardening-final-tests.txt'
    if old.exists():(ROOT/'docs/backend-hardening-full-tests.txt').write_bytes(old.read_bytes())
    skipped=sum(sum(int(n) for n in re.findall(r'skipped=(\d+)',Path(r['log']).read_text(encoding='utf-8'))) for r in final['results'])
    before=read('data/hardening-before.json');oldfiles=before['files'];after={}
    for directory in ('backend','scripts','benchmarks','tests','config'):
        for p in (ROOT/directory).rglob('*'):
            if p.is_file() and p.suffix in {'.py','.toml','.tsv','.json','.sha256'} and '.local.' not in p.name:
                after[p.relative_to(ROOT).as_posix()]=sha(p)
    original_tests={p:h for p,h in oldfiles.items() if p.startswith('tests/')}
    intact=all((ROOT/p).exists() and sha(ROOT/p)==h for p,h in original_tests.items())
    if not intact:raise SystemExit('An original test was changed')
    changed=[p for p in after if p in oldfiles and after[p]!=oldfiles[p]]
    added=[p for p in after if p not in oldfiles]
    core=('backend/domain.py','backend/policy.py','backend/authorization.py','backend/execution/contract.py',
          'backend/execution/gateway.py','backend/verification.py','backend/receipts.py','backend/runtime/taskgraph.py')
    unchanged={p:p in oldfiles and sha(ROOT/p)==oldfiles[p] for p in core}
    if not all(unchanged.values()):raise SystemExit('Frozen financial boundary changed')
    llm=read('docs/llm-holdout-summary.json');reliability=read('docs/backend-reliability-benchmark.json')
    security=read('docs/agent-communication-security-benchmark.json');bank=read('docs/external-bank-evidence-review.json')
    assert security['passed'] and security['tests']==31 and not skipped
    gap='''
## 实际完成状态

| 缺口 | 本轮状态 | 剩余限制 |
|---|---|---|
| LLM 任务规划 | 紧凑协议、真实两模型、独立最终120例已交付 | 合法命中率65.28%/80.56%，非任意语言成熟产品 |
| V3 原子接收 | nonce/audit/evidence/result/outbox ACK 同事务 | Scheduler/state save 仍有独立提交；默认恢复保守停止 |
| 跨进程隔离 | Finance 图读取/summary/anomalies 真 AppContainer | 其他 Agent、旧 Finance/recurring 仍宿主；无 HSM |
| 因果证明 | 签名父证据、编译图、原金融阶段 hash、离线 CLI | 不证明隐藏载荷语义或外部银行签章；旧非图覆盖不完整 |
| 执行竞争/恢复 | 同 DB 所有权、只读两次尝试、结果缓存、正常重启停止 | 无分布式共识、金融 TTL 抢占或自动续跑 |
| 外部银行 | 已有官方沙箱同笔历史证据重新离线验签 | 本轮0新银行HTTP，生产资金/KYC未验证 |
| 公链 | 已有合约/RPC/本地EVM测试保留 | RPC/钱包/部署地址缺失，gas未知 |
| DID/ZKP | 原真实 did:key 与开发 Groth16 通过回归 | 本地信任根/单机setup，不是生产资质 |
| 运行可靠性 | 31专项、640全量、HTTP/锁竞争/1000审计 | 本机有限样本，非生产容量证明 |
'''
    matrix=ROOT/'docs/backend-hardening-gap-matrix.md';text=matrix.read_text(encoding='utf-8')
    if '## 实际完成状态' in text:text=text.split('## 实际完成状态')[0]
    matrix.write_text(text+gap,encoding='utf-8')
    report=f'''# TRUSTA V1 后端深化交付（2026-10-08）

本轮已完成当前可验证范围的架构审查、代码开发、真实双模型评测、进程隔离、因果证据、攻击/恢复测试及全量回归。原六 Agent 与金融安全链保持；没有 UI 改动或新 Agent，没有新增银行付款。**LLM 可用性、全 Agent 进程隔离与外部 Sepolia 仍有明确限制，不能称为生产就绪。**

## 审查与决策

完整阅读两份用户材料，先检查源码/配置/环境/实际测试再设计。普通同用户 subprocess 不足以隔离密钥，因此使用实际 Windows AppContainer；可信 broker 位于宿主，不复制全部 Core 到子进程。原 schema 与金融安全规则不放宽，compact_v2 只减少模型重复元数据。拒绝用签名/包含证明代替银行事实核验。历史指标生成器的疑点经源码排除，没有照搬报告或重新修改已正确逻辑。

本轮没有冻结架构重大冲突。V3 原子接收补丁、Finance transport、handoff evidence 与数据库 graph claim 为兼容附加模块。原 Domain、Policy、Authorization、Execution Contract、Gateway、HardVerifier、Receipt、TaskGraph Validator/Compiler 文件 SHA-256 与修改前一致。

## 新目录与修改

```text
backend/llm/compact_graph.py
backend/runtime/finance_transport.py
backend/runtime/finance_worker.py
backend/runtime/windows_container.py
backend/runtime/handoff_evidence.py
backend/runtime/graph_claims.py
benchmarks/hardening_holdout.py
benchmarks/author_hardening_final_holdout.py
benchmarks/backend_hardening_benchmark.py
benchmarks/backend_*holdout_20261008.json / .sha256
scripts/verify_handoff_evidence.py
scripts/backend_hardening_evidence_review.py
scripts/backend_hardening_delivery.py
tests/test_backend_hardening.py
tests/test_backend_recovery.py
config/llm.compact.example.toml
```

已有修改文件：{', '.join('`'+p+'`' for p in changed if p.endswith('.py'))}。完整文件清单、前后 hash、环境、依赖和输出 hash 见 backend-verification-manifest.json。项目没有 Git 元数据，本轮采用 SHA-256 和原始日志交付，没有虚构 Git commit。

数据流详见 backend-trust-boundaries.md：用户 → Supervisor → Gateway/Router/可替换模型 → 不可信候选 → 原 Validator/grounding → Compiler → Blackboard/Scheduler → 六 Agent。Finance 子进程只经固定 broker 读取与分析。所有金融写继续为 BankActionIR → Schema/Capability → Policy → Authorization → Contract → Gateway → Adapter → HardVerifier → Receipt/Audit。

## 实测结果

修改前真实基线：{baseline['tests']}项 / {baseline['modules']}模块 / {baseline['duration_seconds']:.3f}秒。最终真实全量：**{final['tests']}项 / {final['modules']}模块 / {final['duration_seconds']:.3f}秒 / 0失败 / {skipped}跳过**。原{len(original_tests)}个测试文件字节一致；新增31项。每模块隔离测试进程、4并发，完整原始输出 backend-hardening-full-tests.txt；不是 Agent 隔离证据。

通信/恢复专项31项通过。真实 Child 的 key/DB/source/Adapter/宿主内存/本机网络/staging写访问被拒绝；签名/主体/版本/父引用/replay/存储篡改、撤销/轮换、崩溃、ACK丢失、bounded read retry、整段IO超时与无宿主回退均核对。并发同一授权8次只有1次成功、7次拒绝，生成1张回执。默认 Core 重启不自动恢复运行中的金融任务。

既有六场景基准34/34，预期完成的23/23，跨场景2/2；银行攻击45/45阻止、未授权状态变化0/45。Phase4/5通信基准各自伪造10/10、篡改10/10、重放5/5、审计篡改10/10、Merkle30/30，有限样本未授权执行0/15。均为实际本轮重跑，不是历史数字复述。

可靠性：8次实际独立 Finance 图冷启动往返 p50 {reliability['process_graph']['p50_ms']:.3f}ms / p95 {reliability['process_graph']['p95_ms']:.3f}ms；8并发客户端80请求包含20跨客户拒绝，80/80符合预期，p50 {reliability['http_latency']['p50_ms']:.3f}ms / p95 {reliability['http_latency']['p95_ms']:.3f}ms。1000条新增审计+检查点耗时 {reliability['audit_append_and_checkpoint']['duration_ms']:.3f}ms，回执证明查询/核验20次 p50 {reliability['receipt_proof_query_verify']['p50_ms']:.3f}ms。SQLite另一连接在1秒写锁后成功完成。全部原始采样留存；p95采用样本排序 floor(n*0.95) rank；小样本不外推生产p99/SLA。

真实模型最终两组各120例：DeepSeek合法47/72、全例93/120、缺字段22/24、攻击24/24；Gemini合法58/72、全例96/120、缺字段14/24、攻击24/24。均未授权银行写0。原始响应不等于正确完成；合法待审批也不算付款完成。第一批较低结果全部保留，开发集与最终gold分离，没有依据最终gold反复调分。详见 llm-holdout-evaluation.md、两组完整JSON/CSV与 llm-holdout-summary.json。

## 外部证据和创新范围

已有 Revolut 官方沙箱历史20/20读回记录的回执证明本轮固定公钥验证通过，task/tx/receipt一致；原 MANUAL_REVIEW和后续 VERIFIED均保留，单笔付款claim仍1。本轮银行HTTP0、生产资金操作0。历史证据重验不冒充新银行实测或银行签章。Sepolia缺3个配置，未发链交易；状态单列BLOCKED_BY_EXTERNAL_CREDENTIAL。已有 did:key/Groth16实际测试纳入全量，没有改授权边界；单机开发setup和本地issuer不称为生产密码信任体系。

## 运行、迁移与Demo

默认 full_v1、Virtual Bank 和单进程兼容；新增表/index/triggers按需 CREATE，无删除旧表、数据或nonce，没有迁移旧回执。启用Finance隔离需 Windows/Python3.13与 `TRUSTA_FINANCE_PROCESS_ENABLED=true`；无配置则保持原路径并不得宣称进程隔离。超时默认20s，可配置1–120s。新增宿主staging ACL和AppContainer profile仅用于独立Finance运行资源。新模式回滚先停止相关服务再关闭开关；保留交付/证据/nonce记录，不能清库重发未知付款。

模型示例 config/llm.compact.example.toml 为shadow/public且无密钥，客户敏感请求没有未经审查的数据政策便不路由至外部模型。改模型在Provider/Router配置完成；任意未知HTTP协议仍需相应适配器，没有固定绑定DeepSeek或Gemini。

演示：`python -m benchmarks.backend_hardening_benchmark` 在临时Virtual Bank执行 Finance READ → Summary → Transfer提案 → 独立逐笔确认 → 原全金融安全链 → Receipt → Report，并导出签名因果包。随后 `python -m scripts.verify_handoff_evidence docs/agent-communication-demo.json --trusted-registry docs/agent-communication-trusted-registry.json` 离线验证。本轮两步均真实成功，演示registry须独立固定才形成可信根。六业务场景的实际任务基准 `python -m benchmarks.banking_task_benchmark --output docs/operator-task-run.json`。没有自动调用真实银行或定期模型任务。

## 安全评估与遗留限制

高优先级：LLM仍漏依赖/子任务或错误澄清，合法任务与缺字段质量未达成熟产品指标；下一轮需独立业务人员标注、预先设定成功门槛与新盲样本，不复用最终gold调分。跨进程只覆盖Finance图三类读取和两类分析，其他业务Agent与可信host密钥仍同宿主，未提供HSM/KMS、Linux容器、多主机互信或所有写路径fencing。

证据是历史Runtime声明与审计包含性，不独立证明隐藏业务语义、最新key状态、银行签章或公链。旧非图协作证据覆盖不完整。默认重启不自动续跑；graph claim没有TTL抢占，终态claim维护要先停止旧worker。没有电源中断/磁盘损坏/长期生产压力实验。模型攻击与本机隔离为受控样本，不能宣称100%安全。外部bank/chain缺项不以Virtual/本地EVM代替。

交付下一顺序：扩大独立LLM标注和多轮澄清、为剩余Agent扩展同样窄的进程边界、受控运维恢复与容量验证、具备可信配置后Sepolia独立read-back/真实外部授权证据；保持本轮后端边界，再进入UI/产品形态设计。
'''
    (ROOT/'docs/TRUSTA-V1-backend-hardening-final.md').write_text(report,encoding='utf-8')
    docs=['backend-hardening-architecture-audit.md','backend-hardening-gap-matrix.md','backend-trust-boundaries.md',
          'agent-transport-and-key-isolation.md','agent-handoff-evidence-spec.md','llm-holdout-evaluation.md',
          'agent-communication-security-benchmark.json','agent-communication-recovery-report.json',
          'backend-reliability-benchmark.json','agent-communication-demo.json','agent-communication-trusted-registry.json',
          'external-bank-evidence-review.md','external-bank-evidence-review.json','external-anchor-status.json',
          'historical-llm-metric-review.json','llm-holdout-summary.json','backend-hardening-full-tests.txt',
          'backend-hardening-baseline-tests.txt','llm-final-holdout-deepseek.json','llm-final-holdout-deepseek.csv',
          'llm-final-holdout-gemini.json','llm-final-holdout-gemini.csv','llm-holdout-deepseek.json','llm-holdout-gemini.json',
          'hardening-security-profile4.json','hardening-security-profile5.json','hardening-banking-security.json','hardening-banking-task.json',
          'TRUSTA-V1-backend-hardening-final.md']
    manifest={'created_at_utc':datetime.now(timezone.utc).isoformat(),'git_available':False,'environment':{k:v for k,v in before.items() if k!='files'},
        'baseline':{k:v for k,v in baseline.items() if k!='results'},'final':{k:v for k,v in final.items() if k!='results'},
        'skipped':skipped,'original_test_files':len(original_tests),'original_test_files_byte_identical':intact,'original_test_hashes':original_tests,
        'frozen_boundary_byte_identical':unchanged,'changed_files':changed,'added_or_additionally_inventoried_files':added,
        'before_hashes':oldfiles,'after_source_hashes':after,'artifact_hashes':{'docs/'+p:sha(ROOT/'docs'/p) for p in docs},
        'classifications':{'llm':'REAL_EXTERNAL_VERIFIED_QUALITY_LIMITED','process':'FINANCE_SCOPE_PROCESS_ISOLATION_VERIFIED',
            'bank':'HISTORICAL_OFFICIAL_SANDBOX_EVIDENCE_REVERIFIED','anchor':'BLOCKED_BY_EXTERNAL_CREDENTIAL','production_ready':False},
        'external_bank_http_calls_this_round':0,'production_funds_operations_this_round':0,'new_python_dependencies':[],
        'test_runner':'data/hardening_final_recheck.py','attack_runner':'benchmarks/backend_hardening_benchmark.py'}
    save('backend-verification-manifest.json',manifest)
    print(json.dumps({'tests':final['tests'],'skipped':skipped,'original_tests_intact':intact,'changed_files':changed,'artifacts':len(docs)}))

if __name__=='__main__':main()
