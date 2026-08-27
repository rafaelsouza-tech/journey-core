"""
Catálogo de erros tipados.

Toda exceção de domínio herda de `JourneyCoreError` e carrega `status_code`,
`error_code` (estável, para clientes) e `details` (estruturados, sem PII).
O handler em `core/handlers.py` converte para o envelope `ErrorResponse`.
"""

from typing import Any
from uuid import UUID


class JourneyCoreError(Exception):
    """Base de todas as exceções da aplicação."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# -----------------------------------------------------------------------------
# Genéricas (4xx / 5xx)
# -----------------------------------------------------------------------------


class NotFoundError(JourneyCoreError):
    """404 — recurso inexistente."""

    status_code = 404
    error_code = "NOT_FOUND"

    def __init__(self, resource: str, resource_id: UUID | str) -> None:
        super().__init__(
            f"{resource} '{resource_id}' não encontrado(a)",
            details={"resource": resource, "resource_id": str(resource_id)},
        )


class ConflictError(JourneyCoreError):
    """409 — estado atual não permite a operação."""

    status_code = 409
    error_code = "CONFLICT"


class ForbiddenError(JourneyCoreError):
    """403 — operação não autorizada pelo estado do recurso."""

    status_code = 403
    error_code = "FORBIDDEN"


class ValidationError(JourneyCoreError):
    """422 — entrada semanticamente inválida."""

    status_code = 422
    error_code = "VALIDATION_ERROR"


class ConfigurationError(JourneyCoreError):
    """500 — artefato declarativo (template, plano, regras) inválido no boot."""

    error_code = "CONFIGURATION_ERROR"


# -----------------------------------------------------------------------------
# Pacientes e consentimento
# -----------------------------------------------------------------------------


class PatientNotFoundError(NotFoundError):
    error_code = "PATIENT_NOT_FOUND"

    def __init__(self, patient_id: UUID | str) -> None:
        super().__init__("Paciente", patient_id)


class PatientAlreadyExistsError(ConflictError):
    error_code = "PATIENT_ALREADY_EXISTS"

    def __init__(self) -> None:
        # Sem o telefone na mensagem: respostas de erro não carregam PII.
        super().__init__("Já existe um paciente cadastrado com este telefone")


class ConsentRequiredError(ForbiddenError):
    error_code = "CONSENT_REQUIRED"

    def __init__(self, consent_status: str) -> None:
        super().__init__(
            "Operação exige consentimento ativo (termos aceitos)",
            details={"consent_status": consent_status},
        )


class InvalidConsentTransitionError(ConflictError):
    error_code = "INVALID_CONSENT_TRANSITION"

    def __init__(self, current_status: str, action: str) -> None:
        super().__init__(
            f"Ação '{action}' não é válida a partir do status '{current_status}'",
            details={"from": current_status, "action": action},
        )


# -----------------------------------------------------------------------------
# Protocolos
# -----------------------------------------------------------------------------


class TemplateNotFoundError(NotFoundError):
    error_code = "TEMPLATE_NOT_FOUND"

    def __init__(self, template_id: str) -> None:
        super().__init__("Template de protocolo", template_id)


class SessionNotFoundError(NotFoundError):
    error_code = "SESSION_NOT_FOUND"

    def __init__(self, session_id: UUID | str) -> None:
        super().__init__("Sessão de protocolo", session_id)


class SessionInProgressError(ConflictError):
    error_code = "SESSION_IN_PROGRESS"

    def __init__(self, session_id: UUID, template_id: str) -> None:
        super().__init__(
            "Já existe uma sessão em andamento deste protocolo para o paciente",
            details={"session_id": str(session_id), "template_id": template_id},
        )


class SessionAlreadyCompletedError(ConflictError):
    error_code = "SESSION_ALREADY_COMPLETED"

    def __init__(self, session_id: UUID) -> None:
        super().__init__(
            "Sessão já concluída; não aceita novas respostas",
            details={"session_id": str(session_id)},
        )


class UnexpectedQuestionError(ConflictError):
    error_code = "UNEXPECTED_QUESTION"

    def __init__(self, expected: str, received: str) -> None:
        super().__init__(
            f"Esperava resposta para '{expected}', recebeu '{received}'",
            details={"expected": expected, "received": received},
        )


class InvalidAnswerValueError(ValidationError):
    error_code = "INVALID_ANSWER_VALUE"

    def __init__(self, allowed: list[int]) -> None:
        # Sem o valor recebido: respostas de erro não ecoam a entrada (um número
        # "fora da escala" pode ser um telefone digitado no campo errado).
        super().__init__("Valor fora da escala do protocolo", details={"allowed": allowed})


# -----------------------------------------------------------------------------
# Jornadas
# -----------------------------------------------------------------------------


class JourneyNotFoundError(NotFoundError):
    error_code = "JOURNEY_NOT_FOUND"

    def __init__(self, journey_id: UUID | str) -> None:
        super().__init__("Jornada", journey_id)


class TaskNotFoundError(NotFoundError):
    error_code = "TASK_NOT_FOUND"

    def __init__(self, task_id: UUID | str) -> None:
        super().__init__("Tarefa", task_id)


class TaskAlreadyCompletedError(ConflictError):
    error_code = "TASK_ALREADY_COMPLETED"

    def __init__(self, task_id: UUID) -> None:
        super().__init__("Tarefa já concluída", details={"task_id": str(task_id)})


# -----------------------------------------------------------------------------
# Event Store
# -----------------------------------------------------------------------------


class PIIGuardViolationError(JourneyCoreError):
    """500 — tentativa de gravar PII em um evento. É bug de programação, não erro do cliente."""

    error_code = "PII_GUARD_VIOLATION"

    def __init__(self, event_name: str, violations: list[str]) -> None:
        super().__init__(
            f"Evento '{event_name}' recusado: properties contêm PII",
            details={"event_name": event_name, "violations": violations},
        )
