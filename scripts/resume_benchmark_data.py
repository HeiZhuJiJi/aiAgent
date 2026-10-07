"""Frozen fictional policy facts and queries, authored before measuring results."""

# code | topic | formal fact | English query | Chinese query | required facts
ROWS = """TRV-104|Shanghai hotel reimbursement|The Shanghai hotel reimbursement ceiling is CNY 620 per night.|How much may I claim for one night of accommodation in Shanghai?|到上海出差住酒店，每晚最多能报销多少钱？|620
TRV-205|Domestic travel meal allowance|The domestic business travel meal allowance is CNY 90 per day.|What daily food allowance applies during domestic business travel?|国内出差一天的餐费补助是多少？|90
FIN-318|Expense submission deadline|Expense claims must be submitted within 15 working days after returning from a business trip.|After a work trip, how many working days do I have to file expenses?|出差回来以后，要在多少个工作日内提交报销？|15
PUR-407|Competitive procurement quotation|Purchases exceeding CNY 30000 require at least 3 independent supplier quotations.|Above what purchase amount must I collect competitive quotes, and how many?|采购金额超过多少元需要比价，至少要找几家供应商报价？|30000/3万/30,000;3/三
PUR-512|Supplier admission approval|New suppliers require joint approval by the Procurement Department and the Compliance Department before admission.|Which two departments must authorize adding a new supplier?|新增供应商入库前，需要哪两个部门共同审批？|采购/procurement;合规/compliance
SEC-611|Service account credential rotation|Service account credentials must be rotated every 45 days.|How often must the password or secret of a service account be changed?|服务账号的密码或密钥多久必须轮换一次？|45
SEC-723|Lost corporate laptop reporting|Lost corporate laptops must be reported to Security within 30 minutes of discovery.|What is the reporting deadline after discovering a company laptop is missing?|发现公司笔记本电脑丢失后，最迟多久必须报告？|30/三十
OPS-804|Database backup schedule and retention|The database is backed up every 6 hours and backups are retained for 21 days.|How frequently are database backups created and how long are copies kept?|数据库备份多久执行一次，备份文件保留多少天？|6/六;21
OPS-915|Disaster recovery objectives|The core order service recovery time objective is 40 minutes and its recovery point objective is 10 minutes.|What are the permitted restoration time and data-loss window for the core order service?|核心订单服务的恢复时间目标和恢复点目标分别是多少分钟？|40;10
HR-126|Annual leave carryover|Up to 5 unused annual leave days may carry forward and must be used by March 31 of the next year.|How many unused vacation days can carry over, and when do they expire?|没有休完的年假最多能结转几天，必须在次年什么日期前用完？|5/五;3月31/03-31/March 31
HR-237|New employee security training|New employees must complete 8 hours of security training within the first 14 calendar days.|How much security training must a new hire finish and by which day?|新员工要完成多少小时的信息安全培训，入职多少天内完成？|8/八;14
HR-348|Probation review timing|The probation period is 4 months and its formal review starts 15 days before the end date.|How long is probation and how early should the review start before it ends?|试用期是几个月，转正评估要提前多少天启动？|4/四;15
IT-459|Dormant account suspension|An employee account with no successful login for 60 consecutive days is automatically suspended.|After how many days without a login is an unused employee account disabled?|员工账号连续多少天没有登录会被自动停用？|60
IT-561|Temporary production administrator access|Temporary production administrator access expires automatically after 4 hours.|How long does temporary production admin permission remain active?|临时生产环境管理员权限获批后，多久会自动失效？|4/四
DAT-672|Customer data export authorization|Customer data export authorization is valid for 2 days and exported telephone numbers must be masked.|How long is a customer data export permit valid, and what must happen to phone numbers?|客户数据导出授权有效期是几天，电话号码需要做什么处理？|2/两/二;脱敏/掩码/mask
DAT-783|Audit log retention|Production access audit logs must be retained for at least 180 days.|What is the minimum retention period for production access audit records?|生产环境访问审计日志至少要保留多少天？|180
SVC-894|Critical incident response|A priority-one incident must be acknowledged within 8 minutes with progress updates every 20 minutes.|How quickly must a P1 incident be acknowledged and how often must updates be issued?|一级故障需要在几分钟内响应，之后每隔多久通报一次进展？|8/八;20
VEN-905|Supplier risk reassessment|Active suppliers must undergo a documented risk reassessment every 120 days.|How often must the risk profile of an active supplier be reviewed again?|在用供应商每隔多少天需要重新开展一次风险评估？|120
IT-106|Temporary VPN account lifetime|A visiting engineer temporary VPN account expires after 7 days and requires sponsor approval for renewal.|How long does a visiting engineer temporary remote-network account last before renewal?|外来工程师的临时VPN账号有效期是多少天？|7/七
FIN-217|Invoice discrepancy escalation|An invoice discrepancy above CNY 500 must be escalated to the Finance Controller within 2 working days.|What invoice discrepancy amount triggers escalation and what is the deadline?|发票差额超过多少元必须升级处理，要在几个工作日内上报？|500;2/两/二"""


def dataset():
    documents, queries = [], []
    notes = [
        "Training attendance is recorded by the department coordinator. This note does not specify policy limits or deadlines.",
        "Workflow screens display submission, review and approval history. Consult the formal policy for numeric requirements.",
        "Exception requests must state a business reason and be archived. This note describes supporting records only.",
        "The service desk assists with locating forms. This help article does not replace the formal operating rule.",
    ]
    for row in ROWS.splitlines():
        code, topic, fact, en, zh, expected = row.split("|")
        documents.append(
            {
                "id": code,
                "name": code + ".txt",
                "content": f"Fictional benchmark company. Formal policy {code}. {topic}. {fact} This is the current approved rule for headquarters staff.",
            }
        )
        for index, note in enumerate(notes):
            key = f"NOTE-{code}-{index}"
            documents.append(
                {
                    "id": key,
                    "name": key + ".txt",
                    "content": f"Fictional benchmark company. Supporting note on {topic}. {note}",
                }
            )
        for category, question in [("code", code), ("english", en), ("chinese", zh)]:
            queries.append(
                {
                    "id": f"{code}:{category}",
                    "category": category,
                    "question": question,
                    "gold_document": code,
                    "expected": expected.split(";"),
                }
            )
    return {"schema_version": 1, "synthetic": True, "documents": documents, "queries": queries}
