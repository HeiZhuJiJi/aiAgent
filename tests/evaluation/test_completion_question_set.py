from scripts.evaluate_question_set import evaluate_observation


def test_question_set_projection_does_not_treat_completed_execution_as_quality() -> None:
    result = evaluate_observation(
        {"id": "campus", "question": "有哪些学校？"},
        {
            "task_id": "task-1",
            "state": "COMPLETED",
            "route": {
                "research_plan": {
                    "answer_contract": {"required_aspects": ["学校名单"]}
                }
            },
            "evidence": [],
            "claims": [],
        },
        None,
    )

    assert result["execution_status"] == "COMPLETED"
    assert result["quality_result"]["status"] == "NOT_OBSERVED"
    assert result["quality_result"]["execution_state_is_not_used_as_quality"] is True
    assert result["requirements"][0]["description"] == "学校名单"


def test_question_set_projection_exposes_persisted_partial_delivery_and_gaps() -> None:
    result = evaluate_observation(
        {"id": "weather", "question": "未来一周天气？"},
        {
            "task_id": "task-2",
            "state": "COMPLETED",
            "evidence": [
                {"evidence_id": "e-1", "locator": "https://example.test"}
            ],
            "claims": [],
        },
        {
            "requirements": [
                {"id": "r-1", "description": "逐日覆盖", "mandatory": True}
            ],
            "assessments": [
                {
                    "requirement_id": "r-1",
                    "status": "PARTIALLY_SUPPORTED",
                    "gap_type": "MISSING",
                }
            ],
            "verification": [
                {"id": "v-1", "claim_ids": [], "evidence_ids": ["e-1"]}
            ],
            "delivery_decision": {
                "decision": "PARTIAL_ANSWER",
                "unresolved_requirement_ids": ["r-1"],
            },
            "completion_status": "PARTIAL",
        },
    )

    assert result["quality_result"]["status"] == "PARTIAL"
    assert result["gaps"] == [
        {
            "requirement_id": "r-1",
            "gap_type": "MISSING",
            "status": "PARTIALLY_SUPPORTED",
        }
    ]
    assert result["delivery_decision"]["decision"] == "PARTIAL_ANSWER"
