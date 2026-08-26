# journey-core

Núcleo determinístico de uma jornada de saúde: **consentimento**, **protocolo clínico data-driven** (PHQ-9 com skip logic PHQ-2), **jornada com tarefas**, **trilha de eventos append-only sem PII** e **elegibilidade de follow-up** por regras declarativas.

É o miolo que um agente conversacional usaria por baixo da conversa. Aqui não há chatbot, WhatsApp nem LLM: pontuação, consentimento, trilha e a decisão de "quem recebe um follow-up" são **regras + dados**, auditáveis e reproduzíveis.

> Python 3.12 · FastAPI · Pydantic v2 · pytest · uv (ou pip) · persistência em memória

---

## Sumário

1. [Como rodar](#1-como-rodar)
2. [Roteiro de 15 minutos](#2-roteiro-de-15-minutos)
3. [Endpoints](#3-endpoints)
4. [Arquitetura](#4-arquitetura)
5. [Decisões de desenho](#5-decisões-de-desenho)
6. [Além do pedido: consentimento como ciclo de vida e decisão explicável](#6-além-do-pedido-consentimento-como-ciclo-de-vida-e-decisão-explicável)
7. [Eventos](#7-eventos)
8. [Erros tipados](#8-erros-tipados)
9. [Testes](#9-testes)
10. [Cobertura do enunciado](#10-cobertura-do-enunciado)
11. [O que ficou de fora](#11-o-que-ficou-de-fora)
12. [O 4º dia](#12-o-4º-dia)

---

## 1. Como rodar

### Com uv (recomendado)

```bash
make setup     # uv sync + cria .env com PHONE_HASH_SALT gerado
make dev       # http://localhost:8000 — Swagger em /docs
make test      # suíte pytest (sem rede, sem credenciais)
make demo      # roteiro do revisor, de ponta a ponta, em ~1s
```

`make help` lista todos os alvos. Sem `make`:

```bash
uv sync
cp .env.example .env && sed -i.bak "s/^PHONE_HASH_SALT=.*/PHONE_HASH_SALT=$(openssl rand -hex 32)/" .env
uv run uvicorn app.main:create_app --factory --reload
uv run pytest
```

### Com pip

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e . pytest httpx httpx2   # httpx2 é exigido pelo TestClient do Starlette atual
export PHONE_HASH_SALT=$(openssl rand -hex 32)
uvicorn app.main:create_app --factory --reload
pytest
```

### Com Docker

```bash
make setup          # gera o .env (o compose lê PHONE_HASH_SALT dele)
docker compose up --build
```

A única variável obrigatória é `PHONE_HASH_SALT` (mínimo 16 caracteres). Sem ela a aplicação **não sobe** — um salt esquecido em produção seria incidente, não aviso. As demais estão documentadas em [`.env.example`](.env.example).

---

## 2. Roteiro de 15 minutos

É o critério de "pronto" do enunciado (seção 7), mais um passo 5. `make demo` executa exatamente isto e imprime cada resposta; abaixo, a versão manual com `curl` + `jq` contra `make dev`.

```bash
BASE=http://localhost:8000

# 1. Criar paciente — o telefone vira phone_hash e não volta em nenhuma resposta
PID=$(curl -s -X POST $BASE/patients -H 'content-type: application/json' -d '{
  "phone": "+55 11 90000-0000", "name": "Paciente Exemplo",
  "birth_date": "1990-05-20", "sex": "female", "terms_accepted": true
}' | jq -r .id)

# 2. Iniciar o PHQ-9 e responder até o skip (1 + 1 < 3 → end_block, score parcial = 2)
SID=$(curl -s -X POST $BASE/patients/$PID/protocols -H 'content-type: application/json' \
  -d '{"template_id": "phq9"}' | jq -r .session_id)
curl -s -X POST $BASE/protocol-sessions/$SID/answers -H 'content-type: application/json' \
  -d '{"question_id": "q1", "value": 1}' | jq '{status, next_question: .next_question.id}'
JID=$(curl -s -X POST $BASE/protocol-sessions/$SID/answers -H 'content-type: application/json' \
  -d '{"question_id": "q2", "value": 1}' | tee /dev/stderr | jq -r .journey_id)

#    → ver a jornada criada e concluir uma tarefa
curl -s $BASE/journeys/$JID | jq '{status, objective, tasks: [.tasks[] | {title, status}]}'
TID=$(curl -s $BASE/journeys/$JID | jq -r '.tasks[0].id')
curl -s -X POST $BASE/journeys/$JID/tasks/$TID/complete | jq '.tasks[0].status'

# 3. Avaliar follow-up duas vezes — a segunda cai no cooldown de 72h, com o trace explicando
curl -s -X POST $BASE/followups/evaluate -H 'content-type: application/json' \
  -d "{\"patient_id\": \"$PID\"}" | jq '{eligible, reason, template_key}'
curl -s -X POST $BASE/followups/evaluate -H 'content-type: application/json' \
  -d "{\"patient_id\": \"$PID\"}" | jq '{eligible, reason, cooldown: .trace[-1]}'

# 4. Inspecionar a trilha — só patient_id_hash; sem telefone, nome ou nascimento
curl -s "$BASE/events?patient_id=$PID" | jq '.data[] | {event_name, properties}'
curl -s "$BASE/events?patient_id=$PID" | grep -c "90000-0000\|Paciente Exemplo\|1990-05-20"   # → 0

# 5. (Proposta) Revogar o consentimento: cadastro apagado, trilha intacta, follow-up recusado
curl -s -X POST $BASE/patients/$PID/consent/revoke | jq '{consent_status, name, birth_date}'
curl -s -X POST $BASE/followups/evaluate -H 'content-type: application/json' \
  -d "{\"patient_id\": \"$PID\"}" | jq '{eligible, reason}'
curl -s "$BASE/events?patient_id=$PID" | jq '[.data[].event_name]'
```

Para ver o cooldown **terminar** sem esperar três dias, use `make demo`: ele roda in-process com um relógio controlável e avança 72h no passo 3.

---

## 3. Endpoints

Rotas iguais às sugeridas no enunciado, sem prefixo de versão, para os `curl` acima colarem direto.

| Método | Rota | Corpo | Eventos emitidos |
|---|---|---|---|
| `POST` | `/patients` | `phone, name, birth_date, sex, terms_accepted` | `patient_created` (+ `terms_accepted`) |
| `GET` | `/patients/{id}` | — | — |
| `POST` | `/patients/{id}/consent/{accept\|pause\|resume\|revoke}` | — | `terms_accepted` · `consent_paused` · `consent_resumed` · `consent_revoked` |
| `POST` | `/patients/{id}/protocols` | `template_id` | `protocol_started` |
| `GET` | `/protocol-sessions/{id}` | — | — |
| `POST` | `/protocol-sessions/{id}/answers` | `question_id, value` | `protocol_completed` (+ `journey_created`) |
| `GET` | `/journeys/{id}` · `/patients/{id}/journeys` | — | — |
| `POST` | `/journeys/{id}/tasks/{task_id}/complete` | — | `task_completed` (+ `journey_completed`) |
| `POST` | `/followups/evaluate` | `patient_id` | `followup_eligible` ou `followup_skipped` |
| `GET` | `/events?patient_id=&event_name=` | — | — |
| `GET` | `/health` · `/docs` | — | — |

**`POST …/answers`** devolve sempre o mesmo shape — a próxima pergunta *ou* o resultado final:

```jsonc
{
  "session_id": "…", "template_id": "phq9", "template_version": 1,
  "status": "completed",
  "progress": { "answered": 2, "total": 9 },
  "next_question": null,
  "result": { "score": 2, "max_score": 27, "ended_by_skip": true, "skip_rule_id": "phq2_gate", "answered_questions": ["q1", "q2"] },
  "journey_id": "…"
}
```

**`POST /followups/evaluate`** devolve a decisão *e o porquê* — todas as regras, com o observado e o esperado:

```jsonc
{
  "eligible": false, "reason": "cooldown", "template_key": "checkin_adesao", "rules_version": 1,
  "trace": [
    { "rule_id": "consent",            "check": "consent_status",         "observed": "accepted",     "expected": { "equals": "accepted" },     "passed": true },
    { "rule_id": "protocol_completed", "check": "has_completed_protocol", "observed": true,           "expected": { "equals": true },           "passed": true },
    { "rule_id": "journey_active",     "check": "latest_journey_status",  "observed": "em_andamento", "expected": { "equals": "em_andamento" }, "passed": true },
    { "rule_id": "active_task",        "check": "active_tasks_count",     "observed": 2,              "expected": { "gte": 1 },                 "passed": true },
    { "rule_id": "cooldown",           "check": "hours_since_last_event", "observed": 0.0,            "expected": { "gte": 72 },                "passed": false,
      "details": { "event_name": "followup_eligible", "last_event_at": "…", "unit": "hours", "remaining": 72.0 } }
  ]
}
```

---

## 4. Arquitetura

Camadas `router → service → repository`, organizadas por feature (cada fatia é autocontida). O código de aplicação é síncrono e puro; o estado vive num único container montado por `create_app()`.

```
app/
├── main.py                  create_app() — app factory (uvicorn app.main:create_app --factory)
├── config.py                Settings (pydantic-settings): salt, caminhos dos artefatos, logging
├── container.py             raiz de composição: repositórios, event store, registries, relógio
├── core/
│   ├── clock.py             Clock · SystemClock · FixedClock (testes determinísticos)
│   ├── hashing.py           HMAC-SHA256(salt, telefone normalizado)
│   ├── pii.py               detecção/redação de PII (chaves proibidas + padrão de telefone)
│   ├── exceptions.py        catálogo de erros tipados (status + error_code + details)
│   ├── handlers.py          exceções → envelope ErrorResponse (sem echo de input)
│   ├── logging.py           structlog + processor de redação + request_id por contexto
│   └── middleware.py        request_id + log de acesso (sem body, sem query string)
├── shared/                  BaseSchema, ErrorResponse, InMemoryRepository[T], loaders JSON/YAML
└── features/
    ├── patients/            cadastro + máquina de estados do consentimento
    ├── events/              EventStore (porta append-only) · guarda de PII · GET /events
    ├── protocols/           templates/phq9.json · loader · engine (interpretador puro) · sessões
    ├── journeys/            plans/phq9.json · jornada + tarefas
    └── followups/           rules/default.yaml · checks · engine (trace) · contexto
```

Cada feature segue o mesmo padrão: `models.py` (entidades e schema dos artefatos) · `schemas.py` (contratos Pydantic v2) · `repository.py` · `service.py` · `router.py` · `dependencies.py` (monta o service a partir do container via `Annotated[..., Depends]`).

**Fluxo de um request** — `RequestContextMiddleware` gera `request_id` e o liga ao contexto de log → o router valida o corpo (Pydantic) e injeta o service → o service aplica a regra, muta o repositório e grava o evento no `EventStore` (que valida PII e carimba `correlation_id = request_id`) → o handler de erros converte qualquer `JourneyCoreError` no envelope padrão.

**Artefatos declarativos** — trocar o comportamento clínico ou de produto não exige código:

| Artefato | O que define | Validado no boot |
|---|---|---|
| `features/protocols/templates/*.json` | perguntas, escala, scoring, skip rules | ids únicos, ordens 1..n, regras apontando só para perguntas já respondidas, operadores conhecidos |
| `features/journeys/plans/*.json` | objetivo e tarefas por `template_id` | todo template tem plano (senão a API não sobe) |
| `features/followups/rules/default.yaml` | regras de elegibilidade, prioridade, reasons, `template_key` | checks do vocabulário, params obrigatórios, reasons do enum |

---

## 5. Decisões de desenho

- **Template é a única fonte.** O interpretador (`protocols/engine.py`) só conhece um mini-vocabulário — operandos `sum_of` / `answer` / literal e operadores `lt lte gt gte eq ne` — e a ação `end_block`. Não há `if template_id == …` em lugar nenhum; um teste varre os módulos de lógica e falha se aparecer. Um segundo template com regras diferentes roda sem tocar no serviço (há um teste com um template fictício de 3 perguntas).
- **Pseudonimização por desenho.** O telefone só existe em claro no cadastro operacional. Em eventos, logs e respostas circula `phone_hash = HMAC-SHA256(salt, dígitos)` — HMAC é o "SHA-256 com salt" na construção correta, sem concatenação ingênua; a normalização faz `+55 (11) 90000-0000` e `5511900000000` colidirem no mesmo hash. Nenhum schema de resposta tem o campo `phone`.
- **PII bloqueada na fronteira, não por convenção.** `EventStore.append()` recusa `properties` com chaves proibidas (`phone`, `name`, `birth_date`, …) ou valores com cara de telefone (10–15 dígitos com separadores; datas ISO e UUIDs não disparam). É erro 500 `PII_GUARD_VIOLATION` — bug de programação, não erro do cliente. O mesmo detector alimenta um processor do structlog, e um teste captura os logs de um fluxo completo e afirma que o telefone não aparece nem se alguém logar `phone=` por descuido.
- **Respostas de erro não ecoam a entrada.** O handler de validação devolve só `campo: [mensagens]` — nunca o `input` que o Pydantic inclui por padrão. Testado com um telefone inválido: o 422 não contém o valor.
- **Event store imutável pelo contrato.** A porta `EventStore` expõe `append` e leituras; não existe `update`/`delete` na interface, `Event` é `frozen` e `properties` é `MappingProxyType`. Um teste verifica os três.
- **Minimização.** `protocol_completed` carrega score, máximo, `ended_by_skip`, contagens — não as respostas individuais. `patient_created` carrega só `consent_status`.
- **Relógio injetado.** Todo "agora" vem de `Clock`; nos testes, `FixedClock.advance(hours=72)` prova o cooldown na borda exata (71h59 recusa, 72h00 libera), sem congelar o processo.
- **Regras declaram prioridade e explicam a recusa.** O YAML é avaliado inteiro (sem short-circuit) e a ordem define qual `reason` é reportado. O cooldown (72h) mora só no YAML — não é env var — para ter fonte única.
- **Versão pinada.** A sessão guarda `template_version`; o evento de follow-up guarda `rules_version`. Trocar o JSON/YAML não altera o significado de uma sessão em curso nem de uma decisão passada.
- **Robustez à entrega duplicada.** `POST …/answers` exige o `question_id` esperado (409 `UNEXPECTED_QUESTION` se vier fora de ordem ou repetido); concluir uma tarefa já concluída é 409 sem evento duplicado; só uma sessão em andamento por paciente/template.
- **Jornada só nasce de protocolo concluído.** Não existe `POST /journeys`; a criação é estrutural, e um teste afirma a ausência do endpoint.
- **Rotas `async def`, services síncronos.** Sem I/O não há o que aguardar; com rotas async cada request roda no loop, o que serializa as mutações dos dicionários em memória. Quando a persistência virar Firestore, só a fronteira `repository`/`store` vira `async`.
- **Sem disparo automático de follow-up ao concluir tarefa.** O enunciado permite; preferi o endpoint explícito para o cooldown ser observável no roteiro (um disparo automático o consumiria). Vira flag de configuração quando fizer sentido.
- **Valores de status em português, campos em inglês.** `em_andamento | concluida` são os literais do enunciado; nomes de campos seguem o restante da API (`objective`, `tasks`, `title`).
- **Bibliotecas extras**, além da stack sugerida: `structlog` (logs estruturados com processors — é o que torna a redação de PII um mecanismo, não uma convenção), `pyyaml` (regras com comentários), `httpx`/`httpx2` (só dev: TestClient e `make demo`), `ruff` + `mypy` (qualidade; `mypy --disallow-untyped-defs` torna "type hints nas assinaturas públicas" um gate mecânico).

---

## 6. Além do pedido: consentimento como ciclo de vida e decisão explicável

O enunciado trata consentimento como um booleano de entrada e pede uma trilha imutável. Juntos, esses dois requisitos escondem uma tensão real de engenharia numa healthtech: **imutabilidade vs. direito ao esquecimento** (LGPD art. 18 — exclusão e restrição). Esta implementação resolve a tensão em vez de ignorá-la.

**Consentimento é um estado com transições, não um flag:**

```
pending ──accept──▶ accepted ──pause──▶ paused
   │                   │  ▲                │
   │                   │  └────resume──────┘
   └──────revoke───────┴──────────revoke───┴──▶ revoked (terminal)
```

- `pause` / `resume` implementam a **restrição de tratamento**: nada avança (protocolo, tarefa, follow-up) enquanto pausado, e os dados ficam onde estão.
- `revoke` implementa a **exclusão**: apaga telefone, nome e nascimento do cadastro e emite `consent_revoked` com `erased_fields`. **A trilha de eventos não é tocada** — ela só carrega `phone_hash`, que sem o salt e sem o cadastro é irreversível. O que sobra é uma trilha pseudonimizada, útil para auditoria e analytics, sem o titular.
- Toda transição está numa **tabela** (`CONSENT_TRANSITIONS`); o que não está nela é 409 `INVALID_CONSENT_TRANSITION {from, action}`. Um teste garante que `revoked` é terminal e que nenhum par indefinido é aceito silenciosamente.
- O motor de elegibilidade ganha os reasons `consent_paused` e `consent_revoked` — declarados no YAML via `reason_by_value`, sem código novo.

**A decisão de follow-up é explicável.** `POST /followups/evaluate` avalia todas as regras e devolve o `trace` (observado, esperado, passou, detalhes); o mesmo trace vai para as `properties` do evento. Quem lê a trilha entende *por que* aquele paciente recebeu ou não o follow-up — inclusive quanto faltava para o cooldown acabar. É a contrapartida técnica de dois princípios que a AINA publica: *"você sempre entende por que algo foi sugerido"* e *"pause, exporte ou apague seus dados quando quiser"*.

O roteiro da seção 2 (passo 5) e `tests/integration/test_consent_lifecycle.py` demonstram tudo isso.

---

## 7. Eventos

Envelope: `event_id · occurred_at · event_name · patient_id_hash · properties · schema_version · correlation_id`. O `correlation_id` é o `request_id` do request que originou o evento — liga evento ↔ linha de log. A consulta usa o id interno (`GET /events?patient_id=<uuid>`); o que está persistido é o hash.

| `event_name` | Quando | `properties` |
|---|---|---|
| `patient_created` | cadastro | `consent_status` |
| `terms_accepted` | aceite (no cadastro ou via `consent/accept`) | `source` |
| `protocol_started` | início da sessão | `session_id, template_id, template_version` |
| `protocol_completed` | fim do protocolo, inclusive por skip | `session_id, template_id, template_version, score, max_score, ended_by_skip, skip_rule_id, answered_count, total_questions` |
| `journey_created` | plano criado após o protocolo | `journey_id, source_session_id, template_id, plan_version, task_count` |
| `task_completed` | tarefa concluída | `journey_id, task_id, task_key, remaining_tasks` |
| `followup_eligible` | motor aprovou | `template_key, rules_version, trace` |
| `followup_skipped` | motor recusou | `reason, template_key, rules_version, trace` |
| `consent_paused` / `consent_resumed` | extensão | `previous_status` |
| `consent_revoked` | extensão | `previous_status, erased_fields` |
| `journey_completed` | extensão — última tarefa concluída | `journey_id` |

Os oito primeiros são a taxonomia mínima do enunciado, com os nomes literais; as extensões têm prefixo próprio.

---

## 8. Erros tipados

Envelope único: `{ "success": false, "error": { "code", "message", "details" }, "request_id" }`.

| `code` | HTTP | Quando |
|---|---|---|
| `VALIDATION_ERROR` | 422 | corpo/query inválidos (`details.field_errors`, sem o valor recebido) |
| `PATIENT_NOT_FOUND` · `SESSION_NOT_FOUND` · `JOURNEY_NOT_FOUND` · `TASK_NOT_FOUND` · `TEMPLATE_NOT_FOUND` | 404 | |
| `PATIENT_ALREADY_EXISTS` | 409 | mesmo telefone (mensagem sem o telefone) |
| `CONSENT_REQUIRED` | 403 | iniciar/responder protocolo ou concluir tarefa sem consentimento ativo — `details.consent_status` |
| `INVALID_CONSENT_TRANSITION` | 409 | `details: {from, action}` |
| `SESSION_IN_PROGRESS` · `SESSION_ALREADY_COMPLETED` · `UNEXPECTED_QUESTION` · `TASK_ALREADY_COMPLETED` | 409 | |
| `INVALID_ANSWER_VALUE` | 422 | valor fora da escala (`details.allowed`) |
| `PII_GUARD_VIOLATION` | 500 | tentativa de gravar PII num evento — bug interno, nunca deve ocorrer |
| `CONFIGURATION_ERROR` | — | artefato JSON/YAML inválido: a aplicação não sobe |

---

## 9. Testes

```bash
make test          # tudo (unit + integration + e2e), ~4s, sem rede
make test-cov      # com cobertura; o gate exige ≥ 90% (está em ~97%)
make check         # lint + mypy + cobertura + invariantes do enunciado
```

| Exigência do enunciado (3.5) | Teste |
|---|---|
| Skip logic PHQ-2 — 1+1 encerra com score parcial; 2+1 (= 3) continua; 9 respostas somam | `unit/test_protocol_engine.py`, `integration/test_protocols_api.py` |
| Recusa sem termos (403 tipado) | `test_protocols_api::test_start_without_consent_returns_403_typed` |
| PII ausente nos eventos (e nos logs e nos erros) | `test_events_api`, `unit/test_pii_guard.py`, `integration/test_logs_have_no_pii.py`, `test_patients_api::test_invalid_phone_returns_422_without_echoing_input` |
| Cooldown de 72h | `test_followups_api::test_second_evaluation_within_72h_is_skipped_by_cooldown`, `…::test_eligible_again_after_72h`, `unit/test_rules_engine::test_cooldown_boundary_is_72_hours_inclusive` |
| Jornada criada somente após protocolo | `test_journeys_api::test_no_journey_before_protocol_completion`, `…::test_there_is_no_endpoint_to_create_a_journey_directly` |
| Template é a única fonte | `unit/test_template_loader::test_service_and_engine_have_no_template_specific_branching` (+ comparação literal do JSON com a seção 4 do enunciado) |
| Roteiro do revisor | `e2e/test_reviewer_walkthrough.py` (sequência exata dos eventos) |
| Proposta | `integration/test_consent_lifecycle.py`, `unit/test_consent_transitions.py` |

Cada teste cria sua própria aplicação (`create_app()` com `FixedClock`): não há estado global entre testes.

---

## 10. Cobertura do enunciado

| Seção | Exigência | Onde | Prova |
|---|---|---|---|
| 2 | Camadas api → service → repository reconhecíveis | `app/features/*/{router,service,repository}.py` | estrutura |
| 2 | pytest local sem credenciais de nuvem | in-memory; `.env` só com salt | `make test` |
| 3.1 | `POST /patients` com os 5 campos; 403/409 tipado sem termos | `patients/`, `CONSENT_REQUIRED` | `test_patients_api`, `test_protocols_api` |
| 3.1 | `patient_id` + `phone_hash` SHA-256 com salt via env; telefone nunca em eventos/logs/erros | `core/hashing.py`, `core/pii.py`, `events/pii_guard.py`, `core/handlers.py` | `test_hashing`, `test_pii_guard`, `test_logs_have_no_pii`, `test_events_api` |
| 3.2 | Templates em JSON; PHQ-9 com 9 itens 0–3; skip PHQ-2; interpretador genérico sem `if template_id` | `protocols/templates/phq9.json`, `protocols/engine.py` | `test_template_loader`, `test_protocol_engine` |
| 3.2 | `POST /patients/{id}/protocols` · `POST /protocol-sessions/{id}/answers` → próxima pergunta ou resultado | `protocols/router.py` | `test_protocols_api` |
| 3.3 | Jornada (status/objetivo/tarefas) ao concluir; endpoint de concluir tarefa | `journeys/` | `test_journeys_api` |
| 3.3 | Eventos append-only com envelope mínimo e sem PII; taxonomia de 8 eventos; `GET /events?patient_id=` com hash na trilha | `events/` | `test_events_api`, `e2e` (sequência exata) |
| 3.4 | `POST /followups/evaluate` com regras declarativas (as 5 do enunciado); `followup_eligible` com `template_key`; `followup_skipped` com reason tipado; nenhuma mensagem enviada | `followups/rules/default.yaml`, `followups/engine.py` | `test_rules_engine`, `test_followups_api` |
| 3.5 | Type hints públicos; Pydantic v2; os 5 testes pedidos; sem segredos no repo | `mypy --disallow-untyped-defs`; `schemas.py`; §9; gitleaks + `.gitignore` | `make check`, CI |
| 4 | Enunciado literal, escala literal, pergunta-guia; skip = `end_block`, `ended_by_skip=true`, score parcial; sem fórmulas | `phq9.json` (`scoring.method: sum`) | `test_template_loader::test_phq9_template_matches_the_specification_literally` |
| 5 | Sem WhatsApp/LLM/dashboard/auth/GUI/fórmulas clínicas | `pyproject.toml` (sem SDKs de IA) | `make check` (grep negativo) |
| 6 | Python 3.12, FastAPI, Pydantic v2, pytest, uv ou pip; Docker Compose | `pyproject.toml`, `Dockerfile`, `docker-compose.yml` | CI (`pip install .` também) |
| 7 | Revisor: subir, criar paciente → PHQ → jornada, avaliar 2× com cooldown, `GET /events` sem PII | §1, §2, `make demo` | `e2e/test_reviewer_walkthrough.py` |

---

## 11. O que ficou de fora

Deliberadamente, por estar fora de escopo ou por não caber em três dias com qualidade:

- **Integrações reais** (Meta/WhatsApp, Vertex AI, Firestore, BigQuery, LangGraph, Langfuse), dashboard, autenticação de usuário final, GUI, deploy em GCP.
- **Qualquer lógica clínica além da soma.** Sem índices compostos, sem faixas de severidade, sem sinalização do item 9 — ver o 4º dia.
- **Persistência durável.** Repositórios e event store são dicionários em memória: reiniciar o processo zera tudo, e múltiplos workers do uvicorn não compartilhariam estado. É a escolha do enunciado; as portas (`EventStore`, `InMemoryRepository`) já têm a forma que uma implementação Firestore exporia.
- **Idempotência por chave explícita** (`Idempotency-Key`). Hoje a proteção contra entrega duplicada é semântica (`question_id` esperado, 409 em tarefa já concluída).
- **Paginação em `GET /events`.** A trilha de um paciente é pequena no escopo do teste.
- **Disparo automático de follow-up** ao concluir tarefa (permitido pelo enunciado; ver decisões de desenho).

---

## 12. O 4º dia

Na ordem em que eu atacaria:

1. **Firestore atrás das portas existentes.** `EventStore` e repositórios viram `async`; a coleção de eventos ganha regra de segurança *create-only* para que a imutabilidade valha também no banco, não só no código.
2. **Crypto-shredding.** Uma chave por paciente cifrando o cadastro operacional; `revoke` destrói a chave. O apagamento passa a ser criptograficamente garantido, inclusive em backups.
3. **Contrato de eventos versionado para analytics.** `schema_version` já existe; faltam schemas por `event_name` (Pydantic) validando `properties` no `append` e um export em JSONL/Avro pensado para BigQuery.
4. **Idempotency-Key** em `POST …/answers`, `…/complete` e `/followups/evaluate` — webhooks da Meta reentregam.
5. **Auto-avaliação de follow-up** ao concluir tarefa, atrás de flag, com o mesmo motor.
6. **Decisões clínicas/produto que eu não tomaria sozinho** — só apontaria: um plano de jornada por faixa de score; sinalização determinística do item 9 do PHQ-9 (é prática consolidada, mas o enunciado veta lógica clínica adicional e a AINA se declara não-clínica, então isso é conversa com o time clínico, não código).
7. **Observabilidade**: métricas por `event_name`/`reason` e tracing com `correlation_id` já presente no envelope.

---

### Organização do repositório

```
journey-core/
├── app/                      código de aplicação (ver §4)
├── tests/                    unit · integration · e2e (conftest com app isolada por teste)
├── scripts/demo.py           roteiro do revisor executável
├── Makefile                  make help
├── Dockerfile · docker-compose.yml
├── .github/workflows/ci.yml  ruff · mypy · pytest+cobertura · invariantes · pip install · gitleaks
├── .pre-commit-config.yaml · .gitleaks.toml
└── pyproject.toml · uv.lock · .python-version · .env.example
```
