"""Regras de negócio de cadastro e consentimento."""

from uuid import UUID, uuid4

from app.core.clock import Clock
from app.core.exceptions import (
    ConsentRequiredError,
    InvalidConsentTransitionError,
    PatientAlreadyExistsError,
    PatientNotFoundError,
)
from app.core.hashing import hash_phone
from app.core.logging import get_logger
from app.features.events.models import EventName
from app.features.events.store import EventStore
from app.features.patients.models import (
    CONSENT_TRANSITIONS,
    ConsentAction,
    ConsentStatus,
    Patient,
)
from app.features.patients.repository import PatientRepository
from app.features.patients.schemas import PatientCreateRequest

logger = get_logger(__name__)


def require_active_consent(patient: Patient) -> None:
    """
    Garante consentimento ativo antes de qualquer processamento (protocolo, tarefa, jornada).

    Raises:
        ConsentRequiredError: 403 tipado com o status atual.
    """
    if not patient.has_active_consent:
        raise ConsentRequiredError(patient.consent_status.value)


class PatientService:
    """Cadastro, consulta e ciclo de vida do consentimento."""

    def __init__(
        self, patients: PatientRepository, events: EventStore, clock: Clock, phone_salt: str
    ) -> None:
        self._patients = patients
        self._events = events
        self._clock = clock
        self._salt = phone_salt

    def create(self, data: PatientCreateRequest) -> Patient:
        """
        Cria o paciente; emite `patient_created` e, se houver aceite, `terms_accepted`.

        Raises:
            PatientAlreadyExistsError: telefone já cadastrado.
        """
        phone_hash = hash_phone(data.phone, self._salt)
        if self._patients.get_by_phone_hash(phone_hash) is not None:
            raise PatientAlreadyExistsError()

        now = self._clock.now()
        status = ConsentStatus.ACCEPTED if data.terms_accepted else ConsentStatus.PENDING
        # O `patient_created` nasce antes do cadastro: é ele que identifica a trilha.
        created = self._events.append(
            EventName.PATIENT_CREATED, phone_hash, {"consent_status": status}
        )
        patient = Patient(
            id=uuid4(),
            phone_hash=phone_hash,
            phone=data.phone,
            name=data.name,
            birth_date=data.birth_date,
            sex=data.sex,
            consent_status=status,
            terms_accepted_at=now if data.terms_accepted else None,
            consent_updated_at=now,
            created_at=now,
            trail_start_event_id=created.event_id,
        )
        self._patients.add(patient)
        if data.terms_accepted:
            self._events.append(
                EventName.TERMS_ACCEPTED,
                phone_hash,
                {"source": "registration"},
                trail_id=patient.trail_start_event_id,
            )

        logger.info("patient_created", patient_id=str(patient.id), consent_status=str(status))
        return patient

    def get(self, patient_id: UUID) -> Patient:
        """
        Busca o paciente pelo id interno.

        Raises:
            PatientNotFoundError
        """
        patient = self._patients.get(patient_id)
        if patient is None:
            raise PatientNotFoundError(patient_id)
        return patient

    def apply_consent_action(self, patient_id: UUID, action: ConsentAction) -> Patient:
        """
        Aplica uma ação de consentimento conforme a tabela de transições.

        `revoke` apaga telefone, nome e nascimento do cadastro e libera o telefone para um
        novo cadastro (que terá id e trilha próprios). A trilha deste cadastro permanece
        intacta e endereçável pelo seu id: ela só carrega o hash.

        Raises:
            PatientNotFoundError, InvalidConsentTransitionError
        """
        patient = self.get(patient_id)
        previous = patient.consent_status
        new_status = CONSENT_TRANSITIONS.get((previous, action))
        if new_status is None:
            raise InvalidConsentTransitionError(previous.value, action.value)

        now = self._clock.now()
        patient.consent_status = new_status
        patient.consent_updated_at = now

        if action is ConsentAction.ACCEPT:
            patient.terms_accepted_at = now
            self._events.append(
                EventName.TERMS_ACCEPTED,
                patient.phone_hash,
                {"source": "consent_endpoint"},
                trail_id=patient.trail_start_event_id,
            )
        elif action is ConsentAction.PAUSE:
            self._events.append(
                EventName.CONSENT_PAUSED,
                patient.phone_hash,
                {"previous_status": previous},
                trail_id=patient.trail_start_event_id,
            )
        elif action is ConsentAction.RESUME:
            self._events.append(
                EventName.CONSENT_RESUMED,
                patient.phone_hash,
                {"previous_status": previous},
                trail_id=patient.trail_start_event_id,
            )
        elif action is ConsentAction.REVOKE:
            erased = patient.erase_pii()
            self._patients.release_phone_hash(patient.phone_hash)
            self._events.append(
                EventName.CONSENT_REVOKED,
                patient.phone_hash,
                {"previous_status": previous, "erased_fields": erased},
                trail_id=patient.trail_start_event_id,
            )

        self._patients.save(patient)
        logger.info(
            "consent_updated",
            patient_id=str(patient.id),
            action=str(action),
            consent_status=str(new_status),
        )
        return patient
