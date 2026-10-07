# 通用 Agent 实测报告（2026-09-29）

本次新增可复现的真实依赖评测，不修改生产逻辑，不调整已有断言，不以 mock 结果代替真实调用。结果仅适用于以下环境和小规模测试集，不代表生产 SLA，也不改变项目尚未完成全部发布门禁的结论。

## 环境与固定条件

- Windows；Intel Core i5-12600KF，10 核 / 16 线程；物理内存约 32 GiB。Docker 可见 16 CPU、15.5 GiB 内存。
- 真实 PostgreSQL/pgvector、MinIO、Temporal、Redis；CPU BGE-M3、TEI 1.9.3、float32；模型修订号保存在原始 environment.json。
- 100 份虚构企业制度与相关干扰文档，100 个文本块，20 个政策主题。每个主题有编号、英文改写、中文提问三种形式，共 60 条有标签查询。
- 原文为英文，中文查询用于测量跨语言检索。答案直接位于一个段落内，未覆盖多跳推理或长文档。不是外部公开基准；20 个主题的三种问法并非 60 个完全独立主题。
- 数据和标签在观察结果前固定。候选数 50、RRF k=60、不使用 Reranker；模型与数据库预热，逐次计算真实查询向量，不使用查询向量缓存。
- 检索比较调用现有生产入库、全文检索、向量检索与 RRF 函数，不含完整 API 权限链路或生成模型。
- 部署中 Worker 与本地的回答处理、工作流、操作状态和模型网关四个核心文件 SHA-256 一致；保存在 deployed-worker-code-hashes.json 和 local-code-hashes.json。工作区存在此前未提交修改，不能只用 Git HEAD 代表本次运行代码。

## 检索质量

| 方法 | Top-1 命中 | Recall@3 | Recall@5 | MRR@10 |
|---|---:|---:|---:|---:|
| PostgreSQL simple 全文检索 | 20/60（33.33%） | 33.33% | 33.33% | 0.3333 |
| BGE-M3 向量检索 | 58/60（96.67%） | 100% | 100% | 0.9833 |
| 全文 + 向量 + RRF | 60/60（100%） | 100% | 100% | 1.0000 |

混合检索将两个编号查询的正确文档从第二位提升至第一位；相对向量检索，Top-1 提高 3.33 个百分点。Recall@5 没有提升。样本很小，未证明统计显著或对其他语料普遍优越。

现有全文基线使用 simple 分词和 plainto_tsquery 的 AND 条件，未做关键词改写。英文自然问句多词匹配困难，中文还存在跨语言差异；不能把相对该弱基线的差距写成通用算法提升。分类明细保存在 retrieval_summary.json。

## 检索组件负载

每档将同一组 60 查询重复两次，共 120 请求。闭环负载，计时从获得客户端并发槽之后开始，包含实时向量生成、数据库访问及 RRF，不包含槽位前排队。分位数采用 nearest-rank。

| 并发 | 成功/请求 | P50 | P95 | 成功吞吐 |
|---:|---:|---:|---:|---:|
| 1 | 120/120 | 118.1 ms | 164.5 ms | 8.53 次/秒 |
| 4 | 120/120 | 274.3 ms | 438.2 ms | 13.96 次/秒 |
| 8 | 120/120 | 463.6 ms | 671.5 ms | 16.14 次/秒 |

三档共 360 次请求未观察到请求失败。每档仅约 7–14 秒，是短时观测，不是持续稳定性测试或容量上限测定。吞吐随并发变化不能写成代码优化收益，更不能把该毫秒数写成整个 Agent 的回答耗时。

## 实际故障恢复

| 场景 | 恢复成功 | 平均恢复时间 | P95 | 重复提交/写入 |
|---|---:|---:|---:|---:|
| 实际终止独立 Worker | 5/5 | 14.039 s | 14.057 s | 0 |
| 外部写入成功后丢弃响应 | 20/20 | 27.23 ms | 31.42 ms | 0 |

- Worker：本轮自行启动子进程、使用唯一任务队列；在首步持久化后实际 kill，再启动替代进程。计时包含重启与故障检测，从发出 kill 到工作流返回；每次查询实际数据库，确认两个步骤各提交一次。仅验证两步骤持久工作流，不覆盖完整业务流程的全部故障位置。
- 响应丢失：在真实采购数据库提交成功后不给平台结果；使用生产 UNKNOWN→Reconcile 核对逻辑恢复，再次提交同一请求，逐条核对实际数据库中仅有一条业务记录。计时仅包含 UNKNOWN 记录、结果查询与核对提交，不含最初写入、后续重复请求检查或网络传输延迟。
- 响应丢失实验直接调用生产业务函数及两套数据库，没有通过网络代理丢弃 MCP 响应包；验证的是结果未知后的核对与幂等机制，不能称为完整网络故障演练。
- 25 次受控实验无重复效果，不等于任意故障条件下的零风险保证。共享应用 Worker 未被终止。

## 真实模型端到端问答

真实 Qwen/Qwen3.8-27B；新建隔离工作区，只使用上述虚构语料。固定前 12 个中文问题，每题新会话。计时包含路由、调度、执行及结果返回，每 0.5 秒轮询。Task COMPLETED 不自动算答对。

固定 12 题已全部执行：RAG 路由 12/12，正确原文留存 12/12，成功交付且满足事实检查的回答 **0/12**。截止计时点 11 题 FAILED、1 题仍 RUNNING 并按超时失败计入；随后只读核查数据库，12 题最终均 FAILED，没有本轮遗留的执行中任务。质量验收 **FAIL**。

| 问题编号 | 计时终点状态 | 正确原文留存 | 成功回答 | 本次尝试耗时 |
|---|---|---|---|---:|
| TRV-104 | FAILED | 是 | 否 | 89.540 s |
| TRV-205 | 超时；随后 FAILED | 是 | 否 | 150.417 s |
| FIN-318 | FAILED | 是 | 否 | 147.589 s |
| PUR-407 | FAILED | 是 | 否 | 134.139 s |
| PUR-512 | FAILED | 是 | 否 | 149.532 s |
| SEC-611 | FAILED | 是 | 否 | 148.311 s |
| SEC-723 | FAILED | 是 | 否 | 141.639 s |
| OPS-804 | FAILED | 是 | 否 | 142.897 s |
| OPS-915 | FAILED | 是 | 否 | 136.947 s |
| HR-126 | FAILED | 是 | 否 | 103.742 s |
| HR-237 | FAILED | 是 | 否 | 148.416 s |
| HR-348 | FAILED | 是 | 否 | 141.464 s |

全部尝试平均耗时 136.219 秒、P95 150.417 秒；这是失败/超时尝试的等待时间，不能称为成功回答延迟。没有成功样本，因此成功回答平均延迟和 P95 均为空，不填 0。Task 记录的 Token 合计 119,846，不包含可能独立记账的规划用量；中断调用也未必完整回传使用量，不能据此推导总费用。

逐条结果保存在 20260929-agent-v1/answers.jsonl、summary.json 和 latest_task_states.json。verified-summary.json 从原始记录重新计算指标并验证 12 题全部保留；正确文档留存按预先固定的原文 SHA-256 对照，不用任意关键词碰撞代替。检索命中不代表生成、审查或回答质量通过。

预期事实匹配是预先制定的数值/关键词检查，不等于独立人工语义正确率。所有失败、超时和无答案均保留在分母；Task Token 不包含可能单独记账的规划用量。

失败定位：已保留的尝试记录出现 VALIDATION_FAILED / ANSWER_REPAIR_REQUIRED，指出语义审查不可用。现有审查调用上限为 12 秒；日志可见答案生成和重规划消耗数十秒，部分重规划还触发证据校验拒绝。审查超时是需要验证的候选原因，当前记录不足以断言每次不可用的唯一根因。本轮保留校验与时间预算，没有放宽审查来取得通过数字。

## 复现

在仓库根目录、现有 Compose 服务已启动时运行。每次使用新的输出目录，历史样本不会被覆盖：

    .venv312/Scripts/python.exe -m scripts.run_resume_benchmark --output work/verification/resume-benchmark/<new-retrieval-run>
    .venv312/Scripts/python.exe -m scripts.benchmark_resume_recovery --output work/verification/resume-benchmark/<new-recovery-run>
    .venv312/Scripts/python.exe -m scripts.benchmark_resume_agent --output work/verification/resume-benchmark/<new-agent-run>
    .venv312/Scripts/python.exe -m pytest -q tests/unit/test_resume_benchmark_metrics.py tests/unit/test_retrieval.py

终端状态检查使用 scripts.inspect_resume_benchmark；原始样本复核使用 scripts.summarize_resume_benchmark（--retrieval、--recovery、--agent 指向三个运行目录，--output 指向新的汇总 JSON）。复核会检查样本齐全、标签、失败分母、无重复记录和指标，生成证据文件哈希清单。证据复核通过不代表问答质量通过。

真实模型问答会调用现有配置的 Provider；仅发送脚本内的虚构资料。测试保留新建工作区、知识和审计记录供核验。

## 原始证据与实现映射

- 数据集：scripts/resume_benchmark_data.py；指标定义：scripts/resume_benchmark_metrics.py。
- 检索/负载：scripts/run_resume_benchmark.py → storage/knowledge.py、runtime/knowledge_ingestion.py、runtime/retrieval.py、models/gateway.py。
- 恢复：scripts/benchmark_resume_recovery.py → runtime/workflows.py、storage/operations.py、procurement/service.py，并复用已有真实依赖测试的建数及审批辅助函数。
- 端到端：scripts/benchmark_resume_agent.py → 实际认证 API、路由、research-turns、Temporal Worker、结果和 usage/evidence/completion API。
- work/verification/resume-benchmark/20260929-retrieval-v2/：环境、数据、逐条排名、负载样本、汇总和运行时哈希。
- work/verification/resume-benchmark/20260929-recovery-v1/：逐次任务/操作 ID、进程 ID、数据库记录、计时和 Worker 日志。
- work/verification/resume-benchmark/20260929-agent-v1/：真实回答、失败原因、引用证据、执行步骤和用量。
- work/verification/resume-benchmark/harness-tests.xml：6 项指标及检索函数回归通过。新增脚本和测试的 Ruff 检查通过。
- work/verification/resume-benchmark/verified-summary.json：原始样本齐全性、逐条结果、重算指标与证据哈希；其中 raw_sample_audit=PASS 仅指记录一致，agent.quality_gate=FAIL 是本轮真实问答结论。

## 简历使用建议

优先使用故障恢复数据，写明是受控实验；检索质量可作为第二条，但必须保留“自建 100 份文档、60 条查询”的范围。当前负载测试太短、语料较小，不建议把 16.14 次/秒包装成企业级高并发能力。禁止写“问答准确率 100%”“全部端到端测试通过”或“完整 Agent P95 671 ms”。

可用表述：针对任务中断与重复写入风险，基于 Temporal、幂等控制与结果核对机制实现恢复；在持久化工作流和外部写入场景开展 5 次进程终止、20 次响应丢失实验，全部通过且未发生重复提交或业务写入，进程终止后平均 14.04 秒恢复。

可用表述：构建全文与向量融合检索流程，在包含 100 份虚构制度文档、60 条编号及中英文查询的自建评测集上，混合检索 Top-1 命中率由纯向量的 96.7% 提升至 100%。这只证明本测试集上的两个排序改善，不声称泛化增益。

## 保留的环境问题与修正

首次检索启动时，.env 未显式定义 Compose 默认 embedding 参数，预检中止，没有产生测量样本。修正后读取实际 TEI 信息和宿主机地址，完成 v2；不是改模型或改标签。恢复脚本的初始导入路径在执行前修正，没有减少故障次数。

运行后仅进行导入排序、格式化、预检异步化及负载闭包显式绑定，没有改动数据、指标公式或生产函数。原始运行哈希保留，不冒充后来代码的哈希。

端到端 v1 启动时脚本将“采集结束”视为退出码 0，不代表回答成功；必须读取逐条 success 和完成状态。采集期间已修正后续运行的退出语义：全部预定问题完成且事实检查通过才退出 0，否则退出 1。本次已启动进程仍运行旧代码，原始失败记录保持不变。
