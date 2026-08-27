import pytest
from fastapi.testclient import TestClient

from tests.conftest import CreatePatient, answer, assert_no_pii, run_protocol, start_protocol

pytestmark = pytest.mark.integration


def test_start_returns_first_question_with_literal_scale(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = start_protocol(client, patient["id"])

    assert step["status"] == "in_progress"
    assert step["template_id"] == "phq9" and step["template_version"] == 1
    assert step["progress"] == {"answered": 0, "total": 9}
    question = step["next_question"]
    assert question["id"] == "q1" and question["order"] == 1
    assert question["intro"].startswith("Nas últimas duas semanas")
    assert [o["label"] for o in question["options"]] == [
        "Nenhuma vez",
        "Vários dias",
        "Mais da metade dos dias",
        "Quase todos os dias",
    ]
    assert step["result"] is None and step["journey_id"] is None


def test_start_without_consent_returns_403_typed(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient(terms_accepted=False)
    response = client.post(f"/patients/{patient['id']}/protocols", json={"template_id": "phq9"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert response.json()["error"]["details"] == {"consent_status": "pending"}
    trail = client.get("/events", params={"patient_id": patient["id"]}).json()
    assert "protocol_started" not in [e["event_name"] for e in trail["data"]]


def test_start_unknown_template_returns_404(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    response = client.post(f"/patients/{patient['id']}/protocols", json={"template_id": "gad7"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TEMPLATE_NOT_FOUND"


def test_start_twice_returns_409(client: TestClient, create_patient: CreatePatient) -> None:
    patient = create_patient()
    start_protocol(client, patient["id"])
    response = client.post(f"/patients/{patient['id']}/protocols", json={"template_id": "phq9"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SESSION_IN_PROGRESS"


def test_phq2_skip_completes_with_partial_score(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [1, 1])

    assert step["status"] == "completed"
    assert step["next_question"] is None
    assert step["progress"] == {"answered": 2, "total": 9}
    assert step["result"] == {
        "score": 2,
        "max_score": 27,
        "ended_by_skip": True,
        "skip_rule_id": "phq2_gate",
        "answered_questions": ["q1", "q2"],
    }
    assert step["journey_id"] is not None


def test_sum_of_exactly_3_continues_to_q3(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [2, 1])

    assert step["status"] == "in_progress"
    assert step["next_question"]["id"] == "q3"


def test_full_protocol_scores_plain_sum(client: TestClient, create_patient: CreatePatient) -> None:
    patient = create_patient()
    values = [2, 1, 0, 3, 1, 2, 0, 1, 3]
    step = run_protocol(client, patient["id"], values)

    assert step["status"] == "completed"
    assert step["result"]["score"] == 13
    assert step["result"]["ended_by_skip"] is False
    assert step["result"]["skip_rule_id"] is None
    assert step["progress"] == {"answered": 9, "total": 9}


def test_protocol_events_carry_result_but_not_raw_answers(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    run_protocol(client, patient["id"], [1, 1])
    trail = client.get("/events", params={"patient_id": patient["id"]}).json()
    by_name = {e["event_name"]: e for e in trail["data"]}

    assert by_name["protocol_started"]["properties"]["template_id"] == "phq9"
    completed = by_name["protocol_completed"]["properties"]
    assert completed["score"] == 2 and completed["ended_by_skip"] is True
    assert completed["skip_rule_id"] == "phq2_gate"
    assert completed["answered_count"] == 2 and completed["total_questions"] == 9
    assert "answers" not in completed
    assert_no_pii(client.get("/events", params={"patient_id": patient["id"]}).text)


def test_out_of_order_question_returns_409_with_expected(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = start_protocol(client, patient["id"])
    response = answer(client, step["session_id"], "q2", 1)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "UNEXPECTED_QUESTION"
    assert response.json()["error"]["details"] == {"expected": "q1", "received": "q2"}


def test_duplicate_delivery_of_same_answer_is_rejected(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = start_protocol(client, patient["id"])
    assert answer(client, step["session_id"], "q1", 1).status_code == 200
    response = answer(client, step["session_id"], "q1", 1)

    assert response.status_code == 409
    assert response.json()["error"]["details"]["expected"] == "q2"


@pytest.mark.parametrize("value", [-1, 4, 10])
def test_value_outside_scale_returns_422(
    client: TestClient, create_patient: CreatePatient, value: int
) -> None:
    patient = create_patient()
    step = start_protocol(client, patient["id"])
    response = answer(client, step["session_id"], "q1", value)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ANSWER_VALUE"
    assert response.json()["error"]["details"]["allowed"] == [0, 1, 2, 3]


def test_answer_after_completion_returns_409(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [1, 1])
    response = answer(client, step["session_id"], "q3", 1)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SESSION_ALREADY_COMPLETED"


def test_get_session_returns_same_shape(client: TestClient, create_patient: CreatePatient) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [2])
    response = client.get(f"/protocol-sessions/{step['session_id']}")

    assert response.status_code == 200
    assert response.json()["progress"] == {"answered": 1, "total": 9}
    assert response.json()["next_question"]["id"] == "q2"


def test_unknown_session_returns_404(client: TestClient) -> None:
    response = client.get("/protocol-sessions/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_new_session_allowed_after_completion(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    run_protocol(client, patient["id"], [1, 1])
    second = start_protocol(client, patient["id"])

    assert second["status"] == "in_progress"
