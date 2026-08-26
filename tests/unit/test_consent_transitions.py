import itertools

import pytest

from app.features.patients.models import CONSENT_TRANSITIONS, ConsentAction, ConsentStatus

pytestmark = pytest.mark.unit


def test_transition_table_matches_the_documented_state_machine() -> None:
    assert CONSENT_TRANSITIONS == {
        (ConsentStatus.PENDING, ConsentAction.ACCEPT): ConsentStatus.ACCEPTED,
        (ConsentStatus.ACCEPTED, ConsentAction.PAUSE): ConsentStatus.PAUSED,
        (ConsentStatus.PAUSED, ConsentAction.RESUME): ConsentStatus.ACCEPTED,
        (ConsentStatus.PENDING, ConsentAction.REVOKE): ConsentStatus.REVOKED,
        (ConsentStatus.ACCEPTED, ConsentAction.REVOKE): ConsentStatus.REVOKED,
        (ConsentStatus.PAUSED, ConsentAction.REVOKE): ConsentStatus.REVOKED,
    }


def test_revoked_is_terminal() -> None:
    for action in ConsentAction:
        assert (ConsentStatus.REVOKED, action) not in CONSENT_TRANSITIONS


def test_every_non_terminal_state_can_be_revoked() -> None:
    for status in (ConsentStatus.PENDING, ConsentStatus.ACCEPTED, ConsentStatus.PAUSED):
        assert CONSENT_TRANSITIONS[(status, ConsentAction.REVOKE)] is ConsentStatus.REVOKED


def test_undefined_pairs_are_invalid_not_silently_ignored() -> None:
    defined = set(CONSENT_TRANSITIONS)
    all_pairs = set(itertools.product(ConsentStatus, ConsentAction))
    assert len(all_pairs - defined) == 10
