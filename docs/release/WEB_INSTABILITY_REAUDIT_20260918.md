# Web instability re-audit: 2026-09-18

Scope: deployed configuration, production code, saved live traces and fresh offline reproductions. No production changes or new live-model acceptance in this audit. Not a release PASS.

## Confirmed findings

1. Deployed SearXNG configuration matches repository. Sogou, 360search, Bing, Brave and Google enabled; Baidu and DuckDuckGo disabled. The saved campus natural-query probe returned four results, all from 360search; Brave reported rate limits, Google CAPTCHA, Sogou crash. Two rewrites returned zero. Enabled Bing did not contribute a result. This is captured historical health, not a fresh availability measurement. Artifact: work/verification/nonexclusive-campus-pipeline.json.
2. Actual Baiyun planner included a Guangzhou-only query, losing district scope. Three planner queries yield nine candidate variants in an offline provider expansion; not proof all nine executed. Additional conditional temporal/news searches exist. Multiple queries do not themselves fix backend failure or entity drift.
3. Coverage checkpoint sees results[:6], sharing 6000 passage characters. Ordinary runtime URL merge keeps first occurrence, potentially ignoring later richer copies. Dedicated fetched-page handling is separate. Later new evidence may be outside the checkpoint.
4. Semantic selection sees at most 900 characters per candidate. Generation repacks chosen originals. Question, resolved question and evidence ARE supplied together to generation. Repeated selection/truncation can hide useful facts before generation.
5. Fresh reproduction: historical 1600-character per-source floor exceeds requested 6500 total. Five long sources produce 8000 characters; sixteen produce 25600 BEFORE selector's 900-character preview cap. Actual sixteen-source selector preview content is 14400, not 25600. Generation uses a smaller repacked set. Retained draft sources may append another pack. Artifact: work/verification/context-budget-audit.json.
6. Cache keys omit requested result limit but store final selected results. Offline limit=1 and limit=5 keys collide. Earlier small requests can underfill larger requests. Confirmed defect; not proven cause of captured user failures.
7. Baiyun task 3dcde80e-7b7b-5b24-9bfe-48dbe0632cb0 acquired evidence. Review identified monthly statistics presented as daily measurements and rain/umbrella contradiction. Ten positive-token usage records total 52128 tokens. Final attempt reports insufficient time for assessment. Not simply retrieval failure or overstrict validation. Artifact: work/verification/nonexclusive-live-baiyun.json.
8. Recovery allows generation schema retries, answer revision and replanning. Grounded-path review uses one structured attempt with 384 output tokens; unavailable review becomes answer-repair error. Small allowance is a risk, not proof of truncation. Missing rejected raw responses prevents exact retrospective schema diagnosis.
9. Fetch accepts HTML/plain text/JSON, excludes PDF; HTML text extraction is not browser JavaScript execution. Verified capability boundary, not proof a specific failed source required JavaScript.
10. Topic/source-specific weather, officeholder and sports helpers remain. Earlier destructive historical exclusion and automatic coverage shortcuts were removed. Not all special cases have been removed.

## Remediation order

- Persist per-query engine failures and raw/retained/opened counts. Distinguish service failure from true absence. Bound concurrency and backoff before adding query volume.
- Preserve entity/time/metric constraints across rewrites. Parent queries are discovery leads, not substitute facts. Consolidate planner/provider scheduling and measure new relevant evidence.
- Review complementary evidence rather than first-six order; retain richer duplicate records and preserve table headers, units, entities and dates.
- Enforce one total context budget including retained draft sources; separate preview/generation budgets. Fix cache limit semantics with regression coverage.
- Repair failed stages locally: review-format failure retries review; wrong wording revises existing answer; missing facts triggers targeted search. Reserve budget for final revision/delivery.
- Repeat end-to-end tests across unrelated domains/paraphrases. Measure answer quality separately from retrieval and test pass counts.

## Limits

Saved campus probe is independent of the failed task's exact queries. Bing screenshot shows current weather while prior app task asks historical observations; this does not excuse failure to locate the district page. Latest saved Baiyun/campus acceptance failed; previous DOCX passed. No new service restart or live-model run in this audit. Production repair and screenshot acceptance remain incomplete. This audit does not claim to exclude every possible defect.
