# 通用搜索流程逐项对照（2026-09-18）

状态：INCOMPLETE。这是代码与真实运行的核查记录，不代表逐一证明了所有外部网站、所有执行分支或全部发布门禁。

| 约定 | 实际实现及证据位置 | 结论 |
|---|---|---|
| 完整实体、多级范围不丢失 | runtime/research_scope.py anchor_query；runtime/agent_graph.py entity_scope 为完整目标列表，比较子查询允许选择其中一个完整目标 | 已有机制；目标是否被模型正确识别仍需验收，不能把列表一律当作层级并强拼 |
| 多种关键词，来源不限中文 | agent_graph.py 提示 2–3 查询；web_provider 使用 language=auto；builtins search_one 再加实体锚点 | 已实现有界变体；模型规划超时会阻断入口 |
| 规划失败恢复 | api/app.py 内层只捕获 DomainError，agent_graph 外层 asyncio.timeout 取消内层后才转换 DomainError | 本轮修复：模型调用提前超时，为已有、经过校验的备用规划留出时间；不改变外层总时限 |
| 多引擎候选汇总 | web-provider 调 SearXNG，保留引擎/查询来源和退化诊断、候选多样性 | 不是每个引擎都保证成功；不能将无结果和引擎故障等同 |
| 打开网页获得完整内容 | fetch_public_page 仅 HTML/plain/JSON，4 秒、750000 字节；静态 HTML 提取 | 未实现脚本渲染、图片读数或网页 PDF；字节上限截断没有独立完整性标记；不能声称等同浏览器可见内容 |
| 保持数值与对象/日期/单位关联 | evidence_records、research_scope 的完整日期记录、表格、来源上下文 | 有覆盖；复杂布局、局部上下文裁切仍有限制，不是通用 DOM 语义解析器 |
| 原始正文不被早期永久截断 | provider enrichment 和 runtime merge 保留已抓正文 | 已实现，但受上游抓取限制；需区分原始网页与已抓正文 |
| 问题与证据送给模型 | builtins _analysis_evidence_payload + analysis_prompt；test_model_handler_fencing | 已验证真实请求结构；上下文仍有6500字符预算，有草稿时4500+2000，最多16源 |
| 网页有但模型没收到先重读 | excerpt_manifest、record_manifest、_reread_existing_evidence | 已有一次本地重打包；不是任意分页读取，无法保证所有缺口都从原文找出 |
| 复核一致后再行动 | 原逻辑先 _apply_coverage_findings 再检查一致性，抹去了原始矛盾 | 本轮修复 _coverage_review_error：先纠正原始冲突；遗漏已有事实不能直接触发外部补搜；仍保留错误范围阻断 |
| 被过滤候选重新复查 | 保留80候选，选16摘要、最多3直接打开；HTTPS和URL安全边界保留 | 本轮修复：350字预览按缺口选原文窗口，不再只取开头；候选自身1200字上限仍存在 |
| 保留已有证据、只补不足 | _task_user_context、_load_answer_draft、_load_research_progress | 原始证据存储保留，但搜索恢复上下文只带最近16份；旧相关证据可能被排除。队列仍可能包含初始查询，不能宣称绝对只搜缺口 |
| 七天与今天关系按日期核对 | 规划提示 inclusive end=start+N-1，record_manifest提供包含/遗漏日期 | 没有对任意来源建立完整、统一的需求日期集合与缺口证明；提示不是确定性保证 |
| 重试后交付已支持部分 | supported_draft 保留已支持主张；PARTIAL_ACCEPT只允许来源证明的不可获得部分 | 与“补搜耗尽后明确缺口交付可用部分”尚不一致；不能通过将缺失标MATCH来绕过，需独立终态/展示设计并回归 |
| 预算不足仍留出回答时间 | optional discovery reserve、120秒/80000 tokens | 多次生成/复核仍可能耗尽；未实现按新增信息收益统一停止策略 |
| 用户看到真实失败阶段 | 多种内部错误仍显示通用失败，验收原先只保存HTTP错误 | 本轮验收保留/chat/route的409业务原因；产品侧错误展示尚未全面改造 |

## 本轮修改与验证

- builtins.py：原始复核一致性检查；候选缺口相关原文窗口。
- app.py：模型规划局部时限早于图总时限，现有备用规划仍经过策略验证。非超时异常不吞掉。
- tests/unit/test_review_materiality.py、test_web_candidate_recovery.py、test_chat_routing.py：新增通用回归，不删除或弱化原断言。
- tests/integration/test_model_handler_fencing.py：真实数据库上验证矛盾复核仅纠正复核，已支持答案不重新生成。
- scripts/validate_fixed_recovery.py：保留路由409业务原因，未跳过失败或覆盖旧结果。

已测：前两项修复576单元通过；46合同/数据库通过；新增规划时限后路由39通过；mypy app/builtins通过。最终全量结果补记于下方。

## 实测保留

- recovery-r6：部署启动期间502；没有任务，不算搜索质量验收。
- recovery-r6-ready city_today：COMPLETED，13.321秒；答案使用广州预报/实况，时间与整日/时段解释仍需严格核对，不判全面高质量PASS。
- recovery-r6-ready miami_tomorrow：COMPLETED，18.328秒；给出目标日预报，但额外加入整月区间与当天实况，回答聚焦仍不足。
- recovery-r6-ready district_week/public_science及r6-repeat：部分入口409。新增诊断确认LLM_TIMEOUT / agent Planner exceeded its deadline，发生在搜索前。不能归咎证据校验，也不能算该题完成。
- 所有结果保存在work/verification；后续recovery-r7在服务健康后执行，结果另记。

## 未完成项的优先级

1. 确认规划超时恢复真实进入搜索，防止入口失败被混为检索失败。
2. 显式记录抓取是否截断/是否静态空壳，处理读取不足；不能把未抓到判为不存在。
3. 根据当前缺口选择保留证据，而非最近16份；记录需求/原文/模型上下文/答案四层覆盖差异。
4. 统一有限交付终态，保留安全边界、引用、事实支持；不能靠放松MATCH或删必要测试实现。
5. 多领域、多轮真实验收和普通聊天/RAG/附件回归；单次COMPLETED不能视作稳定。


## 本轮继续修复与最终回归

以下覆盖前文核查时的旧状态，未删除旧失败记录：

- 复核主张编号：真实r7记录出现0基编号，但草稿按1基读取。新增显式claim_index_order，越界/0基/支持与不支持冲突要求复核纠正；草稿对非法范围拒绝保留。已有冲突项排除、其余明确支持项保留的旧测试不变。
- 导航与正文：支持role=navigation/menu/menubar过滤；显式breadcrumb导航保留上下文；独立链接解析仍保留导航链接作发现线索。未增加浏览器或站点分支。
- 菜单排序：整块导航累计关键词会压过正文。改为同一行相关性比较，正文相关长段落优先；NASA失败原文回放从不含成因变为含成因。未增加NASA名称或域名条件。
- 跨轮证据：_retained_evidence_for_gap按当前缺口排序并按内容哈希去重，在16份界限内保留较早但相关的来源，替代单纯取最近16份。
- 选源前预算淘汰：r8迈阿密存储9份来源，旧分配仅留下4份且错失目标来源。preserve_sources=True使初始预览和最终已选源不为塞入第一张大表格而静默丢来源。真实原文回放9→9、4970字符，正确目标页面保留。
- 定向重读：AnswerQualityAssessment.read_requests最多3份已有evidence_id及来源语言focus；未知id拒绝。单次本地重读最多14000字符，为其他原有来源预留空间；仍需生成和再次复核，不自动接受。

测试：588单元通过（targeted-read-unit.xml）；此前46合同/数据库通过（pipeline-db.xml），最终涉及新结构的10项数据库集成再次通过（targeted-read-db.xml）。Ruff和mypy通过。没有删除、跳过或弱化原必要测试。

实测r8：city_today/district_today/district_week/history_week任务完成，Miami/campus/NASA任务失败。不能将四个完成都算高质量：city仍将时段温度概括成当天；history出现无单位降水、来源口径及趋势推断问题。
实测r9：Miami失败；campus完成但混合名单及二手/AI汇总来源未满足验收；NASA完成且回答核心排列关系，但新增频率等旁支仍需事实检查，不判全部高质量通过。
普通聊天r8：DIRECT，2.836秒，解释RAG；实测文件recovery-r8-chat.json。
已授权三份HR文档r9：任务COMPLETED且旧脚本quality_ok=true，但人工检查发现“三天”的审批边界表述错误/自相矛盾，因此质量FAIL。禁止将该脚本布尔值作为实际RAG质量PASS。该问题尚未修复。

所有真实结果保留在work/verification/recovery-r8-*.json、recovery-r9-*.json和recovery-r9-rag.jsonl；r10为定向重读部署后的复测，结果另记。

仍未完成：动态页面渲染/公开XHR读取；超大网页抓取截断标记；统一需求日期覆盖清单；补搜耗尽后的可信有限交付；低质量二手/AI来源的充分识别；RAG数值条件边界；跨轮重复高质量验收。当前总状态仍INCOMPLETE。


## r10 部署后实测与最终状态

- Miami：COMPLETED，17.491秒，目标日温度已输出并标明预报/来源差异；晴雨描述仍缺，不能判完整高质量PASS。
- 黄埔周预报：FAILED，33.456秒。首个失败为已有证据包含9月23日但答案遗漏；随后修复耗尽预算，复核未完成。说明定向读取不能单独解决生成遗漏与修复开销。保留失败，不将r8成功覆盖此失败。
- NASA：COMPLETED，26.409秒，核心成因/排列关系可回答；频率/类型等附加表述仍需逐项核实，不以COMPLETED声明全答案正确。
- 最新API和worker已部署定向重读版本；web-provider已部署导航角色/面包屑处理。用户不需要再次自行重启来使这些修改生效。
- 截图标准仍未全部达到；还需修复已有证据遗漏后的低开销答案补全、来源和时间范围过度概括、学校名单来源质量、RAG条件边界。没有报告PROJECT COMPLETE。


## 统一链路修复续轮（r11–r13）

本轮按共同链路修改，未加入城市、网站、天气题字符串分支：

- `AnswerRepairPatch`：精确原文替换/追加与局部主张替换；拒绝重复匹配、重叠区间、越界索引、未知来源和伪造引文。补丁只产生待复核候选，不能赋予通过状态。旧引用检查与独立最终复核保留。
- `citation_quotes`：先前复核仅收到主张与来源ID，原始引文被省略。现在独立传递去重后的主张索引—来源ID—引文表，保持精简主张结构及旧测试，避免复核重新猜测对应记录。
- REVISE仅在有明确read_requests时重读；普通修答不重新选择所有来源窗口。REPLAN保留定向本地重读和外部补搜。
- `_best_verbatim_quote`：每条来源支持原先被要求包含整条多来源主张的全部数字，错误拒绝分布在不同来源中的事实。现在只要求修复后的连续原文保留该引文本身的数字；完整主张的语义支持仍独立复核。
- 公开页面内application/json和application/ld+json解析：保留原始嵌套记录、空值、日期和单位；不执行脚本，不改变原始URL，不读取认证数据。尚未实现浏览器渲染/XHR捕获。
- fetch_truncated/read_mode：抓取字节边界→提供方→web.search输出合同→持久化调用结果→任务上下文→生成及复核；与发布方事实分开。静态读取和抓取截断不能证明远程页面无更多记录。
- 原始需求与规划建议：复核不再继承required_aspects和条目数量作为强制验收项目。按用户原始问题的粒度判断缺口，未要求的定量指标和目录不能触发补搜。所有实际事实仍需要来源支持。
- 预算：生成后预留28秒，重生成后预留20秒用于复核/提交；按用户此前增加预算的授权，联网及混合任务界面与验收统一120000 tokens/180秒，验收等待190秒。没有通过提高上限替代事实校验。

验证：607单元通过（scope-repair-unit.xml）；130网页+合同+真实数据库测试通过（acquisition-db.xml）；22前端测试通过。生产Python Ruff/mypy通过。所有原有测试保留；预算断言随实际配置同步，未降低断言强度。

真实r11：city_today/district_today/miami_tomorrow/campus完成，district_week/history_week/public_science失败。完成不等于质量全通过，city包含宽泛范围/额外推断。
真实r12：district_week/history_week失败，public_science完成但包含多余目录介绍和非NASA链接，不判全质量通过。周预报本轮实际暴露规划将“天气”扩张到逐日降水量，以及单条支持被强制要求含多来源全部数字；对应修复在r12之后。

### 更正此前HR三天边界判断

原始HR-02第2页同时规定“超过1天但不超过3天，由部门负责人审批”和“连续请假3天以上，在部门负责人审批后还需人力资源部复核”。因此不能把“三天仅部门审批”作为唯一正确答案。此前r9报告直接认定附加HR复核错误，判断不充分。应解释基础审批与连续天数附加复核的关系，保持原文的包含边界。r12真实RAG已完成（recovery-r12-rag.jsonl），但表述为两种口径仍不够清楚；本轮进一步加入通用累加条件/边界解释要求。未篡改源文件。

仍不得声明全部完成：动态渲染、失败后的可信有限交付终态、稳定重复截图验收尚未证明；原有版本失败记录全部保留。r13部署结果另记。


## 统一修复 r14 与独立复核 r15（真实失败不计 PASS）

代码/测试映射：

| 通用改动 | 实现 | 验证 |
| --- | --- | --- |
| 结构有效但结论与覆盖矛盾时，恢复为 REVISE/REPLAN，绝不恢复为通过 | capabilities/builtins.py::_recover_rejected_review | unit/test_review_materiality.py；未知来源、越界索引仍拒绝 |
| 失败后保留逐条复核且对象匹配的局部事实，保持失败终态、幂等提交、不进入后续记忆 | runtime/conversation_results.py::_partial_review_message/_supported_partial_reply | unit/test_web_task_result.py；integration/test_conversation_turns.py |
| 图像 alt、语义图标 aria-label/title 保留在当前单元格，不猜图标编码，不恢复导航 | apps/web_provider/main.py::_VisibleTextParser | unit/test_web_provider.py；真实 Miami 页面包含 clear/thunderstorm/rain showers 标签 |
| 搜索 503 的限流、验证码、后端异常原因跨接口保留，避免都变成无结果 | provider SearchResponse；runtime/web_provider.py | unit/test_web_provider_diagnostics.py |
| 完整外语别名独立检索，原始对象范围继续用于证据复核 | contracts/domain.py::BoundedResearchPlan.entity_aliases；runtime/agent_graph.py；api/app.py；runtime/research_scope.py；builtins.py | unit/test_research_scope.py 覆盖规划→API→持久化→执行；父级缩写及未知键拒绝 |
| 生成仍使用保留证据和补搜进度；独立复核不再接收前轮缺口和搜索词作为验收要求 | builtins.py::_assess_answer_quality | integration/test_model_handler_fencing.py 保留生成进度/审计/引用/提交断言，并验证复核隔离 |

r14：618 单元通过（work/verification/r14-unit.xml），25 数据库/API/合同通过（r14-db2.xml），9 个生产文件 Ruff/mypy 通过。首次数据库运行有 2 个 Windows ProactorEventLoop 环境失败，保留 r14-db.xml 并遮蔽连接凭据；切换 Psycopg 支持的 SelectorEventLoop 后原测试全部通过。r15 独立复核修改后相关 58 项单元/真实数据库回归通过（r15-review.xml），生产文件 Ruff/mypy 通过。未删除、跳过、xfail 或放宽原来源/权限/fencing/提交断言。

真实 r14（work/verification/recovery-r14-*.json）：
- city_today COMPLETED 15.376 秒；district_today COMPLETED 10.284 秒。城市回答仍含不必要的来源差异解释，不据此宣布全量质量通过。
- miami_tomorrow FAILED 46.559 秒：实际读取 QWeather 的 Oklahoma 同名地点及 NWS 宽泛页面；复核正确识别对象错误，后续引用修复/预算未完成。不能把这次失败归咎于缺少中文来源。
- district_week FAILED 44.531 秒：已保留9月18–24日七天温度；这次不是只有六天，而是复核追加逐日降雨概率/量的强制要求。终态保留了通过逐条复核的温度记录，任务仍是 FAILED。
- history_week FAILED 47.705 秒：9月12–13日记录保留；补搜又取得9月7日，但生成遗漏且后续修复未完成。逐日记录覆盖不足，不能用两天替代整周。
- campus FAILED 36.644 秒：来源集中于广州大学，不能回答整个大学城名单。部分结果仅保留广州大学校区事实，不能算名单成功。
- public_science COMPLETED 25.529 秒：NASA 原文/中文解释链路可用，但把同地点约375年一次的全日食频率泛化到所有日食，质量仍不合格。
- 普通聊天 DIRECT，能解释RAG；脚本 quality_ok 不等于每句解释完美。
- 已授权三份HR文档 task ec7f2c82-97a2-57b7-a7eb-0a7a0fe91ab8 COMPLETED，但把“连续3天以上”改为“连续超过3天”。即使旧脚本 quality_ok=true，人工验收仍 FAIL；不篡改原制度来迎合答案。

外部服务实际观测：SearXNG 返回 brave too many requests、google CAPTCHA、sogou unexpected crash，并有 /v1/search 503。过程健康检查不能证明搜索引擎可用。没有绕过验证码；已询问用户是否有现成搜索API配置，尚未得到答复。

当前仍 INCOMPLETE：真实截图问题未全部稳定达标；公开动态DOM/XHR读取尚未实现；原DOCX本轮未重发；定量要求扩张、历史已有记录遗漏、政策阈值包含边界、低质量/宽泛来源排名仍需继续修复和实测。API/worker 已更新到 r15，web-provider 保持 r14。不声明 PROJECT COMPLETE，不将有限交付算完整答案通过。r15真实结果另附。


### r15 部署后原失败题复测

- Miami task 583c62dd-35a5-518c-bfd3-0087e8f94807 COMPLETED 22.434秒：已取得Florida逐日气温/天气，但把2026-09-19写为周五（实际周六），存在来源年份/日期匹配风险；人工质量FAIL，不能通过改星期掩盖来源时效问题。
- 黄埔周预报 task a158a1d0-372b-59db-b1ce-9287d0cba7cc COMPLETED 16.281秒：提供9月18–24日七天温度和天气现象，未再强求逐日定量降水；仍重复列出两个移动页面差异及当天实况，不能据单次完成证明稳定。
- 大学城 task ce0a0601-e194-5eff-98b3-28019033dcea COMPLETED 16.264秒：提供首批十校并明确2004年/小谷围岛范围，但没有当前扩展区域完整名单，尚不满足冻结完整名单标准。
- 运行状态：api、worker、web-provider、web均running/healthy。用户不需要自行重启使本轮修改生效。
- 保留全部r14/r15原始结果，不覆盖失败；整体验收仍INCOMPLETE。r15未重测历史题、NASA、HR和DOCX，不将此前完成状态移作本版本质量PASS。
