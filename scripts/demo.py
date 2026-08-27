"""
Demonstração de ponta a ponta: cadastro → protocolo → jornada → follow-up → trilha → revogação.

    make demo                                            # in-process, relógio controlável
    make demo ARGS="--base-url http://localhost:8000"    # contra a API no ar

Cada passo imprime a chamada, o status e os campos que importam; ao final, a trilha
de eventos — para conferir, na tela, que não há telefone, nome ou nascimento.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.clock import FixedClock

JSON = dict[str, Any]

FAKE_PATIENT = {
    "name": "Paciente Exemplo",
    "birth_date": "1990-05-20",
    "sex": "female",
    "terms_accepted": True,
}


class DemoFailure(Exception):
    """Algo prometido pelo README não aconteceu."""


def banner(text: str) -> None:
    print(f"\n\033[1;34m== {text}\033[0m")


def show(label: str, response: httpx.Response, *fields: str) -> JSON:
    body: JSON = response.json()
    picked = {field: body.get(field) for field in fields} if fields else body
    print(f"  {label:<48} HTTP {response.status_code}")
    if picked:
        print("   " + json.dumps(picked, ensure_ascii=False, default=str))
    return body


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise DemoFailure(message)


def build_client(base_url: str | None) -> tuple[httpx.Client, FixedClock | None]:
    if base_url:
        return httpx.Client(base_url=base_url, timeout=10), None

    from fastapi.testclient import TestClient

    from app.config import AppEnv, Settings
    from app.main import create_app

    clock = FixedClock(datetime.now(tz=UTC))
    settings = Settings(
        PHONE_HASH_SALT=secrets.token_hex(32),
        APP_ENV=AppEnv.DEVELOPMENT,
        LOG_LEVEL="ERROR",
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings=settings, clock=clock)), clock


def fake_phone() -> str:
    return f"+55 11 9{secrets.randbelow(10_000):04d}-{secrets.randbelow(10_000):04d}"


# -----------------------------------------------------------------------------
# Passos da demonstração
# -----------------------------------------------------------------------------


def step_create_patient(client: httpx.Client, payload: JSON) -> str:
    banner("1. Criar paciente (telefone vira hash; nunca volta na resposta)")
    patient = show(
        "POST /patients",
        client.post("/patients", json=payload),
        "id",
        "phone_hash",
        "consent_status",
    )
    expect("phone" not in patient, "a resposta não deveria conter o telefone")
    patient_id: str = patient["id"]
    return patient_id


def step_run_protocol(client: httpx.Client, patient_id: str) -> JSON:
    banner("2. Responder o PHQ-9 até o skip (1 + 1 < 3 → end_block)")
    step = show(
        "POST /patients/{id}/protocols",
        client.post(f"/patients/{patient_id}/protocols", json={"template_id": "phq9"}),
        "status",
        "progress",
    )
    print(f"   próxima pergunta: {step['next_question']['id']} — {step['next_question']['text']}")
    for value in (1, 1):
        qid = step["next_question"]["id"]
        step = show(
            f"POST /protocol-sessions/{{id}}/answers  ({qid}={value})",
            client.post(
                f"/protocol-sessions/{step['session_id']}/answers",
                json={"question_id": qid, "value": value},
            ),
            "status",
            "progress",
            "result",
        )
    expect(
        step["status"] == "completed" and step["result"]["ended_by_skip"], "skip PHQ-2 não encerrou"
    )
    journey = show(
        "GET /journeys/{id}  (criada ao concluir)",
        client.get(f"/journeys/{step['journey_id']}"),
        "status",
        "objective",
    )
    print("   tarefas: " + ", ".join(f"{t['title']} [{t['status']}]" for t in journey["tasks"]))

    banner("2b. Concluir uma tarefa")
    task_id = journey["tasks"][0]["id"]
    show(
        "POST /journeys/{id}/tasks/{task_id}/complete",
        client.post(f"/journeys/{journey['id']}/tasks/{task_id}/complete"),
        "status",
    )
    return journey


def step_evaluate_followup(client: httpx.Client, patient_id: str, clock: FixedClock | None) -> None:
    banner("3. Avaliar follow-up duas vezes e observar o cooldown de 72h")
    evaluate = lambda: client.post("/followups/evaluate", json={"patient_id": patient_id})  # noqa: E731
    first = show("POST /followups/evaluate  (1ª)", evaluate(), "eligible", "reason", "template_key")
    second = show("POST /followups/evaluate  (2ª)", evaluate(), "eligible", "reason")
    cooldown = second["trace"][-1]
    print(
        f"   trace[cooldown]: observed={cooldown['observed']}h expected={cooldown['expected']} "
        f"remaining={cooldown['details'].get('remaining')}h"
    )
    expect(first["eligible"] is True, "a primeira avaliação deveria ser elegível")
    expect(second["reason"] == "cooldown", "a segunda avaliação deveria cair no cooldown")
    if clock is not None:
        clock.advance(hours=72)
        third = show(
            "POST /followups/evaluate  (+72h, relógio avançado)", evaluate(), "eligible", "reason"
        )
        expect(third["eligible"] is True, "após 72h o paciente deveria voltar a ser elegível")
    else:
        print("   (contra API no ar o relógio é real — pule 72h para ver a reabilitação)")


def step_inspect_trail(client: httpx.Client, patient_id: str, payload: JSON) -> int:
    banner("4. Inspecionar GET /events — sem PII")
    trail = client.get("/events", params={"patient_id": patient_id}).json()
    for event in trail["data"]:
        props = json.dumps(event["properties"], ensure_ascii=False)[:90]
        print(f"   {event['occurred_at']}  {event['event_name']:<20} {props}")
    raw = json.dumps(trail, ensure_ascii=False)
    leaked = [v for v in (payload["phone"], payload["name"], payload["birth_date"]) if v in raw]
    print(f"   PII na trilha: {leaked if leaked else 'NENHUMA ✓'}")
    expect(not leaked, "PII encontrada na trilha")
    total: int = len(trail["data"])
    return total


def step_revoke_consent(client: httpx.Client, patient_id: str, events_before: int) -> None:
    banner("5. Revogar consentimento: cadastro apagado, trilha intacta")
    revoked = show(
        "POST /patients/{id}/consent/revoke",
        client.post(f"/patients/{patient_id}/consent/revoke"),
        "consent_status",
        "name",
        "birth_date",
    )
    expect(
        revoked["name"] is None and revoked["birth_date"] is None,
        "o cadastro deveria estar apagado",
    )
    decision = show(
        "POST /followups/evaluate  (após revogação)",
        client.post("/followups/evaluate", json={"patient_id": patient_id}),
        "eligible",
        "reason",
    )
    expect(
        decision["reason"] == "consent_revoked",
        "follow-up deveria ser recusado por consent_revoked",
    )
    after = client.get("/events", params={"patient_id": patient_id}).json()["data"]
    print(
        f"   eventos antes: {events_before} · depois: {len(after)} · "
        f"últimos: {[e['event_name'] for e in after[events_before:]]}"
    )
    expect(len(after) == events_before + 2, "a trilha deveria ter crescido, nunca encolhido")


def main() -> int:
    parser = argparse.ArgumentParser(description="Demonstração de ponta a ponta da API")
    parser.add_argument("--base-url", help="URL da API no ar (padrão: in-process)")
    args = parser.parse_args()
    client, clock = build_client(args.base_url)
    payload: JSON = {**FAKE_PATIENT, "phone": fake_phone()}

    try:
        patient_id = step_create_patient(client, payload)
        step_run_protocol(client, patient_id)
        step_evaluate_followup(client, patient_id, clock)
        events_before = step_inspect_trail(client, patient_id, payload)
        step_revoke_consent(client, patient_id, events_before)
    except DemoFailure as failure:
        print(f"\n\033[1;31m✗ {failure}\033[0m")
        return 1

    print("\n\033[1;32m✓ demonstração concluída\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
