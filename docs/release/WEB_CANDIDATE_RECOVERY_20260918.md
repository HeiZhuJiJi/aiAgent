# Candidate reinspection and recovery repair - 2026-09-18

## Implemented

- Search provider returns a bounded, separate candidate pool for soft-filtered/unselected HTTPS sources. Reasons distinguish lexical/temporal filtering from ranking/result limits. Sources below the returned-result cap remain available as leads. Candidate summaries are not factual evidence.
- Runtime retains candidate metadata in normal capability output, committed through existing executor fencing. Replanning reloads only successful web.search results for the same task/workspace. No new database or permission path.
- Existing coverage assessment receives at most 16 bounded lead previews. Missing coverage can select those URLs for normal SSRF-protected fetching before more engine search. Across replans, this check precedes new queries. Successful candidate reads are reassessed; failed reads leave normal search available.
- Detail links discovered on opened pages are also retained as unopened leads; link labels never count as source facts.
- Ordinary duplicate URL merging can keep richer content. Larger cached result sets can satisfy smaller requests, but smaller result sets cannot underfill larger requests.
- Historical evidence packing respects the overall content-character budget. Previously supported draft sources share the generation budget (4500 + 2000), rather than append beyond 6500.

## Implementation and tests

Production: apps/web_provider/main.py; src/enterprise_agent/runtime/web_provider.py; src/enterprise_agent/capabilities/builtins.py.
New regressions: tests/unit/test_web_candidate_recovery.py; tests/integration/test_web_candidate_archive.py. Test-only provider support: tests/fakes/web_provider.py.
Existing required assertions retained. Final regression: 502 passed in 19.13 seconds, including all unit tests plus candidate archive, fencing, replanning, terminal handling, usage and budget integration tests. Artifact: work/verification/candidate-recovery-final.xml. Ruff and mypy production checks passed before final live probes.
API, worker and provider rebuilt/restarted; healthy after initial deployment. The last detail-link change updated API/worker.

## Live observations

- Campus: 32c007b1-4758-5738-99a7-211e59709e6a COMPLETED, 12.3 seconds, seven evidence items. Answer distinguishes ten island universities from twelve including the south-bank universities. One successful run does not establish repeated reliability; answer includes an additional historical sentence which was not independently accepted as a quality metric.
- Original authorized DOCX: cbbea567-9b17-57b6-b86e-3a628e8d5662 COMPLETED, 5.168 seconds; existing live quality checks passed.
- Initial Baiyun before detail-link retention: 0155645d-10c5-5c99-aec7-e623a745053b FAILED, 31.549 seconds, eight evidence items. It located current pages and historical links but did not answer the requested week. Recovery archive persisted 14 then 18 candidates. Total charged tokens 51477 against 50000; failure detail named step budget, but actual used steps were 3 against 100. Preserve this failure; do not claim the candidate archive alone fixes answer quality.
- Final Baiyun/Huangpu results are recorded in work/verification/candidate-recovery-live-baiyun-links.json and candidate-recovery-live-huangpu.json when their probes finish.

## Limits

Candidate pools and previews are bounded, not exhaustive archives. Unsafe/malformed URLs are not revived. HTTP recovery links remain excluded under the existing HTTPS boundary. This change does not fix upstream engine availability, browser-only rendering, all topic-specific heuristics, or every semantic interpretation error. A stored candidate is not a guaranteed usable source. Overall screenshot stability and release gates remain INCOMPLETE until measured live acceptance supports otherwise.


## Additional confirmed extraction defect

The final link-recovery probe 525ae047-f90b-5e5d-bc6f-15cdbeb07f01 still failed (55.901s, 51862 tokens). Original evidence f143995c contains the requested September 8 daily record, but the focused excerpt prioritized navigation and aggregate statistics. The date-row branch only recognized standalone month/day/year lines. It now recognizes year-first hyphen/slash/Chinese dates and trailing weekday or table text. It preserves whole requested records and column headings under the content budget instead of falling back to navigation when the entire table does not fit. Regression uses generic inventory records in four date formats, not a weather-specific source. Saved actual-record replay: work/verification/date-row-replay.txt. Production excerpts include required_aspects with resolved dates; replaying only the original relative-date utterance is not equivalent.

Huangpu live probe 433721a5-46bb-531b-80ba-acebf9105d54 COMPLETED (21.37s, 12 evidence items). It supplies a seven-day temperature outline but explicitly lacks last-day weather/wind detail. One cited source is city-level; do not count this as proof of fully verified district-level quality. The general collection/answer pipeline completed, but full scope/quality acceptance remains partial.


## Final live outcome after date-row repair

Baiyun task 516d5919-db36-56b3-be43-8a317daf7279 FAILED (28.535s, four evidence items, 50176 tokens). Review explicitly acknowledged supported September 7-10 district facts, but still required wording revision; recovery then could not assess within its remaining budget. This confirms the date-row repair reaches useful facts but does NOT establish end-to-end success. No additional bypass of evidence review was introduced. Remaining priority is distinguishing material correction from optional stylistic feedback and reserving recovery budget. Final artifact: work/verification/candidate-recovery-live-baiyun-final.json. API/worker/provider all healthy; services already restarted by this task. Overall screenshot stability remains INCOMPLETE.
