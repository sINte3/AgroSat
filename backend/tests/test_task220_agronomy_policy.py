from datetime import date
import pytest

from services.agronomy_policy import evaluate, quality, recommend


def observation(value=.4, when='2026-08-01', **changes):
    return {'id': 1, 'value': value, 'date': when, 'cloud': 5., 'valid': 95., **changes}


def test_identical_inputs_produce_identical_reviewable_recommendations():
    evidence = {'as_of': '2026-08-04', 'finding': {'cause_code': 'nutrient_deficiency'},
                'baseline': observation(), 'season': {'season_year': 2026}, 'freshness': {'status': 'FRESH'}}
    result = recommend(evidence)
    assert result == recommend(evidence)
    assert result['suggestions'][0]['category'] == 'sampling'
    assert result['requires_approval'] is True
    assert result['confidence'] == 'moderate'
    assert result['policy_version'] == 'r3-f-v1'


def test_missing_evidence_is_explicit_and_never_invented():
    result = recommend({'as_of': '2026-08-04'})
    assert result['confidence'] == 'low'
    assert len(result['limitations']) == 4
    assert result['suggestions'][0]['category'] == 'reinspection'


@pytest.mark.parametrize(('change','status'), [(.05,'IMPROVED'),(.049,'NO_MATERIAL_CHANGE'),(-.05,'WORSENED'),(-.049,'NO_MATERIAL_CHANGE')])
def test_material_threshold_boundaries(change, status):
    result = evaluate(observation(), observation(.4+change, '2026-08-13'), '2026-08-05', '2026-08-13')
    assert result['status'] == status
    assert result['delta'] == round(change,6)


@pytest.mark.parametrize('when', ['2026-08-01','2026-08-05','2026-08-12'])
def test_precompletion_and_wait_boundary_scenes_rejected(when):
    with pytest.raises(ValueError, match='window'):
        evaluate(observation(), observation(.7, when), '2026-08-05', '2026-08-20')


def test_wait_and_provider_pending_are_distinct():
    assert evaluate(observation(), None, '2026-08-05', '2026-08-12')['status']=='TOO_EARLY'
    assert evaluate(observation(), None, '2026-08-05', '2026-08-13')['status']=='PENDING_DATA'
    assert evaluate(observation(), None, '2026-08-05', '2026-08-13', freshness='PROVIDER_DEGRADED')['status']=='PROVIDER_DEGRADED'


@pytest.mark.parametrize(('changes','status'), [({'cloud':31},'CLOUD_BLOCKED'),({'valid':49},'QUALITY_BLOCKED'),({'value':float('nan')},'QUALITY_BLOCKED'),({'cloud':None},'QUALITY_BLOCKED'),({'value':1.1},'QUALITY_BLOCKED')])
def test_quality_cannot_masquerade_as_improvement(changes, status):
    assert quality(observation(**changes))==status
    assert evaluate(observation(), observation(when='2026-08-13', **changes), '2026-08-05','2026-08-13')['status']==status


def test_field_average_does_not_resolve_local_zone():
    result = evaluate(observation(), observation(.8,'2026-08-13'), '2026-08-05','2026-08-13', zone_required=True)
    assert result['delta']==.4
    assert result['status']=='INCONCLUSIVE'
    assert result['statistics_scope']=='field'


def test_stale_or_missing_baseline_is_inconclusive():
    for baseline in [None, observation(when='2026-07-01')]:
        assert evaluate(baseline, observation(.8,'2026-08-13'), '2026-08-05','2026-08-13')['status']=='INCONCLUSIVE'
