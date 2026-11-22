from decimal import Decimal
import pytest
from app.errors import DataError
from app.modules.domain.rule import evaluate


def test_cross_day_sample_proves_rollover(cases):
    amount, proof = evaluate(cases['night_split'])
    assert amount == Decimal('1497.500')
    labels = {label: value for label, value, _ in proof}
    assert labels['0–4000 跨日'] == '+86400000'
    assert '跨日样本 4000' in labels


def test_cross_day_date_is_matched_to_drop(cases):
    amount, _ = evaluate(cases['night_date'])
    assert amount == Decimal('1497.500')


def test_cross_day_sample_with_date(cases):
    amount, proof = evaluate(cases['night_dated'])
    assert amount == Decimal('1497.500')
    assert dict((label, value) for label, value, _ in proof)['跨日样本 4000'] == '2026-11-19'


def test_unproven_rewind_stays_quarantined(cases):
    with pytest.raises(DataError) as error:
        evaluate(cases['night_unproven'])
    assert error.value.code == 'SOUND_REWIND'


def test_cross_day_bad_date(cases):
    with pytest.raises(DataError) as error:
        evaluate(cases['night_bad_date'])
    assert error.value.code == 'DATE'


def test_cross_day_mismatch(cases):
    # A sample pinned to a boundary that did not rewind cannot be a rollover.
    with pytest.raises(DataError) as error:
        evaluate(cases['night_no_drop'])
    assert error.value.code == 'SOUND_WRAP'


def test_cross_day_duplicate(cases):
    with pytest.raises(DataError) as error:
        evaluate(cases['night_duplicate'])
    assert error.value.code == 'SOUND_WRAP'


def test_regular_rewind_unchanged(cases):
    with pytest.raises(DataError) as error:
        evaluate(cases['rewound'])
    assert error.value.code == 'SOUND_REWIND'
