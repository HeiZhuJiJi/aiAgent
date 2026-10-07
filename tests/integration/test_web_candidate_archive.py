import json
from uuid import uuid4

from enterprise_agent.capabilities import builtins
from enterprise_agent.storage.tables import CapabilityCallRow, TaskRow
from tests.integration.test_model_handler_fencing import _request, _seed, database  # noqa: F401


async def test_recovery_loads_only_successful_same_task_candidates(database):  # noqa: F811
    workspace, task, step, attempt, _ = await _seed(database)
    other_workspace, other_task, other_step, other_attempt, _ = await _seed(database)
    lead = {'url': 'https://records.example/detail', 'title': 'Detail',
            'snippet': 'Untrusted lead, not evidence.', 'reason': 'ranking_or_result_limit'}
    async with database.begin() as session:
        row = await session.get(TaskRow, task)
        goal = dict(row.goal_payload)
        goal['user_context'] = json.dumps({'recovery_query': 'missing detail'})
        row.goal_payload = goal
        for ws, tid, sid, aid, state, url in [
            (workspace, task, step, attempt, 'SUCCEEDED', lead['url']),
            (workspace, task, step, attempt, 'FAILED', 'https://failed.example/'),
            (other_workspace, other_task, other_step, other_attempt, 'SUCCEEDED', 'https://other.example/'),
        ]:
            session.add(CapabilityCallRow(id=uuid4(), workspace_id=ws, task_id=tid,
                step_id=sid, attempt_id=aid, operation_id=uuid4(), command_id=uuid4(),
                request_hash='a' * 64, request_payload={}, capability_id='web.search',
                capability_version='1.0.0', policy_decision='ALLOW', policy_reason='test',
                policy_version='1', state=state, trace_id='candidate-archive-test',
                result_payload={'output': {'candidates': [{**lead, 'url': url}], 'attempted_queries': [url]}}))
    request = _request(workspace, task, step, attempt).model_copy(update={'capability_id': 'web.search'})
    context = await builtins._task_user_context(database, request)
    assert context['discovery_candidates'] == [lead]

    assert context["previously_attempted_queries"] == [lead["url"]]
