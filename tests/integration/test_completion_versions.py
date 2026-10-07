from datetime import UTC, datetime
from uuid import uuid4

from enterprise_agent.contracts.domain import ContractVersion, Requirement, RequirementType
from enterprise_agent.storage.completion import (
    create_contract_version,
    create_requirement,
    get_current_contract_version,
    list_requirements,
)
from enterprise_agent.storage.tables import TaskContractVersionRow
from tests.integration.completion_fixtures import database, prepare  # noqa: F401


async def test_contract_v2_supersedes_current_version_without_erasing_v1(database):  # noqa: F811
    fixture = await prepare(database)
    version_two = ContractVersion(
        id=uuid4(),
        workspace_id=fixture.workspace_id,
        task_id=fixture.task_id,
        version=2,
        supersedes_id=fixture.contract.id,
        content_hash="d" * 64,
        goal="Produce a narrower verified report",
        completion_policy={"allow_partial": False, "version": "completion.v2"},
        created_by=fixture.user_id,
        created_at=datetime.now(UTC),
    )
    requirement_two = Requirement(
        id=uuid4(),
        workspace_id=fixture.workspace_id,
        task_id=fixture.task_id,
        contract_version_id=version_two.id,
        key="narrowed-report",
        type=RequirementType.ANALYSIS,
        mandatory=True,
        description="The narrower report is verified.",
        completion_rule="The v2 verification gates pass.",
    )
    async with database.begin() as session:
        await create_contract_version(session, version_two)
        await create_requirement(session, requirement_two)

    async with database() as session:
        assert await get_current_contract_version(session, fixture.workspace_id, fixture.task_id) == version_two
        all_requirements = await list_requirements(session, fixture.workspace_id, fixture.task_id)
        current_requirements = await list_requirements(
            session,
            fixture.workspace_id,
            fixture.task_id,
            contract_version_id=version_two.id,
        )
        assert {item.id for item in all_requirements} == {fixture.requirement.id, requirement_two.id}
        assert current_requirements == (requirement_two,)
        assert await session.scalar(
            select_count(TaskContractVersionRow, fixture.workspace_id, fixture.contract.id)
        ) == 1


def select_count(model, workspace_id, row_id):
    from sqlalchemy import func, select

    return select(func.count()).select_from(model).where(
        model.workspace_id == workspace_id,
        model.id == row_id,
    )
