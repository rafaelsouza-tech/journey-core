"""
Roteiro do revisor, executável de ponta a ponta.

    make demo                         # in-process, com relógio controlável (mostra o fim do cooldown)
    make demo ARGS="--base-url http://localhost:8000"   # contra a API no ar

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

FAKE_PATIENT = {
    "phone": "+55 11 90000-0000",
    "name": "Paciente Exemplo",
    "birth_date": "1990-05-20",
    "sex": "female",
    "terms_accepted": True,
}


def banner(text: str) -> None:
    print(f"\n\033[1;34m== {text}\033[0m")


def show(label: str, response: httpx.Response, *fields: str) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    picked = {field: body.get(field) for field in fields} if fields else body
    print(f"  {label:<48} HTTP {response.status_code}")
    if picked:
        print("   " + json.dumps(picked, ensure_ascii=False, default=str))
    return body


def build_client(base_url: str | None) -> tuple[httpx.Client, Any]:
    if base_url:
        return httpx.Client(base_url=base_url, timeout=10), None

    from fastapi.testclient import TestClient

    from app.config import AppEnv, Settings
    from app.core.clock import FixedClock
    from app.main import create_app

    clock = FixedClock(datetime.now(tz=UTC))
    settings = Settings(
        PHONE_HASH_SALT=secrets.token_hex(32),
        APP_ENV=AppEnv.DEVELOPMENT,
        LOG_LEVEL="ERROR",
        _env_file=None,  # type: ignore[call-arg]
    )
    return TestClient(create_app(settings=settings, clock=clock)), clock


def main() -> int:
    parser = argparse.ArgumentParser(description="Roteiro do revisor (seção 7 do enunciado)")
    parser.add_argument("--base-url", help="URL da API no ar (padrão: in-process)")
    args = parser.parse_args()
    client, clock = build_client(args.base_url)
    phone_suffix = secrets.randbelow(10_000)
    patient_payload = {
        **FAKE_PATIENT,
        "phone": f"+55 11 9{phone_suffix:04d}-{secrets.randbelow(10_000):04d}",
    }

    banner("1. Criar paciente (telefone vira hash; nunca volta na resposta)")
    patient = show(
        "POST /patients",
        client.post("/patients", json=patient_payload),
        "id",
        "phone_hash",
        "consent_status",
    )
    pid = patient["id"]

    banner("2. Responder o PHQ-9 até o skip (1 + 1 < 3 → end_block)")
    step = show(
        "POST /patients/{id}/protocols",
        client.post(f"/patients/{pid}/protocols", json={"template_id": "phq9"}),
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

    banner("3. Avaliar follow-up duas vezes e observar o cooldown de 72h")
    first = show(
        "POST /followups/evaluate  (1ª)",
        client.post("/followups/evaluate", json={"patient_id": pid}),
        "eligible",
        "reason",
        "template_key",
    )
    second = show(
        "POST /followups/evaluate  (2ª)",
        client.post("/followups/evaluate", json={"patient_id": pid}),
        "eligible",
        "reason",
    )
    cooldown = second["trace"][-1]
    print(
        f"   trace[cooldown]: observed={cooldown['observed']}h expected={cooldown['expected']} remaining={cooldown['details'].get('remaining')}h"
    )
    if clock is not None:
        clock.advance(hours=72)
        show(
            "POST /followups/evaluate  (+72h, relógio avançado)",
            client.post("/followups/evaluate", json={"patient_id": pid}),
            "eligible",
            "reason",
        )
    else:
        print("   (contra API no ar o relógio é real — pule 72h para ver a reabilitação)")
    assert first["eligible"] and second["reason"] == "cooldown"

    banner("4. Inspecionar GET /events — sem PII")
    trail = client.get("/events", params={"patient_id": pid}).json()
    for event in trail["data"]:
        print(
            f"   {event['occurred_at']}  {event['event_name']:<20} {json.dumps(event['properties'], ensure_ascii=False)[:90]}"
        )
    raw = json.dumps(trail, ensure_ascii=False)
    leaked = [
        v
        for v in (patient_payload["phone"], patient_payload["name"], patient_payload["birth_date"])
        if v in raw
    ]
    print(f"   PII na trilha: {leaked if leaked else 'NENHUMA ✓'}")

    banner("5. Proposta — revogar consentimento: cadastro apagado, trilha intacta")
    before = len(trail["data"])
    show(
        "POST /patients/{id}/consent/revoke",
        client.post(f"/patients/{pid}/consent/revoke"),
        "consent_status",
        "name",
        "birth_date",
    )
    show(
        "POST /followups/evaluate  (após revogação)",
        client.post("/followups/evaluate", json={"patient_id": pid}),
        "eligible",
        "reason",
    )
    after = client.get("/events", params={"patient_id": pid}).json()["data"]
    print(
        f"   eventos antes: {before} · depois: {len(after)} · últimos: {[e['event_name'] for e in after[before:]]}"
    )

    print("\n\033[1;32m✓ roteiro concluído\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
