// Deterministic GET /api/management-analytics responses in the exact TASK_232
// contract shape (definitions_version management_analytics_v1). The fixture is
// internally consistent: states sum to active_problems.total, verified +
// unverified = resolved cycles, and breakdown rows use the same field set.
// Numbers are chosen so each figure is distinguishable in the rendered page.

export const DEFINITIONS_VERSION = 'management_analytics_v1';
export const ENTERPRISES = Object.freeze([
  { id: 7, name: 'Альфа Агро' },
  { id: 8, name: 'Бета Хлопок' },
  { id: 9, name: 'Гамма Зерно' },
]);
export const CROPS = Object.freeze([
  { id: 301, name: 'Хлопок' },
  { id: 302, name: 'Пшеница' },
]);

const clone = (value) => JSON.parse(JSON.stringify(value));

function remediationTotals(overrides = {}) {
  return {
    needs_inspection: 0, inspection_active: 0, awaiting_review: 0, awaiting_decision: 0,
    plan_active: 0, work_active: 0, awaiting_satellite_verification: 0, verification_blocked: 0,
    improved_awaiting_closure: 0, not_improved: 0, reopened: 0, ...overrides,
  };
}

function breakdownMetrics({ monitored = 1, active = 0, states = {}, overdueCases = 0, overdueItems = 0, period = {} } = {}) {
  return {
    monitored_fields: monitored,
    current: {
      active_problems: active,
      by_remediation_status: remediationTotals(states),
      overdue_cases: overdueCases,
      overdue_work_items: overdueItems,
    },
    period: {
      inspections_opened: 0, resolved_cycles: 0, improved: 0, unchanged: 0, worsened: 0,
      unverified: 0, reopen_events: 0, ...period,
    },
  };
}

export function fieldRows(count, { enterpriseId = 7, enterpriseName = 'Альфа Агро' } = {}) {
  return Array.from({ length: count }, (_, index) => {
    const crop = CROPS[index % CROPS.length];
    return {
      ...breakdownMetrics({
        active: Math.max(0, 3 - Math.floor(index / 3)),
        states: index === 0 ? { needs_inspection: 2, work_active: 1 } : {},
        period: index === 0 ? { inspections_opened: 2, resolved_cycles: 1, improved: 1 } : {},
      }),
      field_id: 1000 + index,
      field_name: `Поле ${String(index + 1).padStart(3, '0')}`,
      enterprise_id: enterpriseId,
      enterprise_name: enterpriseName,
      current_crop_type_id: index === count - 1 ? null : crop.id,
      current_crop_name: index === count - 1 ? null : crop.name,
    };
  });
}

function weekBuckets() {
  const rows = [
    ['2026-08-28', '2026-08-30'], ['2026-08-31', '2026-09-06'], ['2026-09-07', '2026-09-13'],
    ['2026-09-14', '2026-09-20'], ['2026-09-21', '2026-09-26'],
  ];
  return rows.map(([start, end], index) => ({
    bucket_start: start,
    bucket_end: end,
    anomaly_candidates_detected: index === 4 ? 4 : 0,
    inspections_opened: [0, 2, 3, 4, 6][index],
    plans_drafted: [0, 1, 2, 3, 4][index],
    plan_cycles_approved: [0, 1, 2, 2, 4][index],
    plan_cycles_work_completed: [0, 0, 2, 2, 3][index],
    plan_cycles_verified: [0, 0, 1, 2, 2][index],
    resolved_cycles: [0, 0, 1, 2, 3][index],
    improved: [0, 0, 1, 0, 1][index],
    unchanged: [0, 0, 0, 1, 0][index],
    worsened: [0, 0, 0, 0, 1][index],
    unverified: [0, 0, 0, 1, 1][index],
    closed: [0, 0, 1, 1, 1][index],
    returned_for_rework: [0, 0, 0, 1, 2][index],
    reopen_events: [0, 0, 0, 1, 1][index],
  }));
}

function duration(start, end, anchor, population, samples, median, p90) {
  return {
    start_event: start,
    end_event: end,
    period_anchor: anchor,
    population,
    sample_count: samples,
    median_hours: samples ? median : null,
    p90_hours: samples >= 10 ? p90 : null,
    status: samples ? 'measured' : 'no_samples',
  };
}

/** The manager snapshot of enterprise 7 (TASK_232 example extended). */
export function managerSnapshot() {
  return {
    definitions_version: DEFINITIONS_VERSION,
    generated_at: '2026-09-26T12:21:55.036036+05:00',
    timezone: 'Asia/Tashkent',
    scope: { role: 'manager', authorization: 'tenant', enterprise_id: 7, field_id: null, current_crop_type_id: null },
    crop_classification: {
      basis: 'current_crop_season',
      reference_year: 2026,
      rule: 'each field is classified once by its latest crop_seasons row with season_year <= reference_year (the Operational Center rule)',
      historical_crop_at_event: false,
    },
    period: {
      requested: { date_from: null, date_to: null },
      effective: {
        date_from: '2026-08-28', date_to: '2026-09-26', inclusive: true, days: 30,
        starts_at: '2026-08-28T00:00:00+05:00', ends_before: '2026-09-27T00:00:00+05:00', granularity: 'week',
      },
    },
    provenance: {
      lifecycle: 'task220_canonical_remediation',
      inspection_workflow: 'task217_canonical_inspection',
      status_projection: 'task225_remediation_status',
      case_model: 'task221_operational_center_cases',
      verification_policy_version: 'r3-f-v1',
      sources: ['fields', 'crop_seasons', 'field_inspections', 'autonomous_anomaly_candidates', 'alerts', 'agronomy_plans',
        'agronomy_work_items', 'agronomy_verifications', 'agronomy_events', 'satellite_field_freshness', 'satellite_collection_runs'],
      excluded_legacy_sources: ['corrective_actions', 'action_verification_requests'],
      definitions_fingerprint: '91bca1b614782a938742b28a71db41d21a9a0fc916a942479f4964540211ea05',
    },
    coverage: {
      fields_in_scope: 14,
      monitored_fields: 13,
      inactive_fields: 1,
      monitored_fields_with_active_problems: 12,
      ndvi_freshness: {
        fresh: 9, aging: 2, stale: 1, never_collected: 0, cloud_blocked: 1, provider_degraded: 0, quality_blocked: 0, not_evaluated: 0,
      },
    },
    current: {
      as_of: '2026-09-26T12:21:55.036036+05:00',
      active_problems: {
        total: 15,
        fields_affected: 13,
        by_source: { inspection: 13, candidate: 1, alert: 1 },
        by_priority: { critical: 1, high: 3, normal: 10, low: 1 },
        legacy_open_inspections: 1,
      },
      by_remediation_status: {
        needs_inspection: { total: 3, inspections: 1, candidates: 1, alerts: 1, unassigned: 1 },
        inspection_active: 1,
        awaiting_review: 1,
        awaiting_decision: 1,
        plan_active: { total: 2, draft: 1, approved: 1 },
        work_active: 1,
        awaiting_satellite_verification: { total: 1, pending_data: 1, too_early: 0 },
        verification_blocked: { total: 1, cloud_blocked: 0, quality_blocked: 1, provider_degraded: 0, inconclusive: 0 },
        improved_awaiting_closure: 1,
        not_improved: { total: 2, unchanged: 1, worsened: 1 },
        reopened: 1,
      },
      plans_pending_verification: 5,
      // The TASK_232 overdue case: main work item on time, secondary item late.
      work_items: { active: 3, planned: 1, in_progress: 2, unassigned: 0, overdue_work_items: 1 },
      overdue_cases: { total: 0, inspection_stage: 0, work_stage: 0 },
      data_unavailable: { freshness_cases: 2, external_cases: 0 },
    },
    period_activity: {
      anomaly_candidates_detected: 4,
      inspections_opened: { total: 15, manual: 12, alert: 2, pixel_ndvi: 1 },
      inspections_confirmed: 11,
      inspections_rejected: 1,
      inspections_cancelled: 0,
      plans_drafted: 10,
      plan_cycles_approved: 9,
      plan_cycles_work_completed: 7,
      plan_cycles_verified: 5,
      plans_cancelled: 0,
    },
    outcomes: {
      resolved_cycles: 6,
      verified: { improved: 2, unchanged: 1, worsened: 1, total: 4 },
      unverified: {
        total: 2, pending_data: 0, too_early: 0, cloud_blocked: 1, quality_blocked: 0, provider_degraded: 0, inconclusive: 1,
      },
      closed: { total: 3, improved: 2, without_improvement: 1 },
      returned_for_rework: 3,
      reopen_events: { total: 2, after_closure: 1, after_verification: 1 },
    },
    cycle_times: {
      unit: 'hours',
      p90_minimum_samples: 10,
      metrics: {
        signal_to_inspection_opened: duration('autonomous_anomaly_candidates.created_at (satellite signal detected)', 'field_inspections.created_at (canonical inspection opened from that candidate)', 'field_inspections.created_at', 'canonical inspections opened from an autonomous anomaly candidate', 3, 5.25, null),
        inspection_opened_to_reviewed: duration('field_inspections.created_at (inspection opened)', 'field_inspections.reviewed_at (finding confirmed or rejected)', 'field_inspections.reviewed_at', 'canonical inspections reviewed (confirmed or rejected)', 11, 30.5, 70.25),
        inspection_submitted_to_plan_drafted: duration('field_inspections.submitted_at (finding submitted)', "agronomy_plans.created_at of the inspection's first plan", 'agronomy_plans.created_at of the first plan', 'inspections whose first TASK_220 plan was drafted', 10, 2.5, 8.75),
        plan_approved_to_work_completed: duration("agronomy_events 'approve' of the plan cycle", "agronomy_events 'work_complete' that moved the cycle to pending_verification", 'work completion event time', 'plan cycles whose required work was completed', 7, 96, null),
        work_completed_to_verified: duration('agronomy_verifications.completed_at (cycle work completion)', "agronomy_verifications.created_at of the cycle's first conclusive verification", 'first conclusive verification time', 'plan cycles with a conclusive IMPROVED, NO_MATERIAL_CHANGE or WORSENED verification', 5, 212.4, null),
        case_opened_to_verified_closure: duration('field_inspections.created_at (canonical case root opened)', 'agronomy_plans.closed_at (plan closed with a conclusive verification)', 'agronomy_plans.closed_at', 'plans currently closed whose closing verification is conclusive', 0, null, null),
      },
      unsupported: [{
        metric: 'alert_signal_to_inspection_opened',
        reason: 'alerts.triggered_at is stored without a time zone, so no timezone-safe persisted signal time exists for alert-origin cases',
      }],
    },
    completion: {
      work_completion: {
        population: "plan cycles approved in the period (agronomy_events 'approve')",
        numerator: 7, denominator: 9, rate: 0.7778, completed: 7, ended_without_completion: 0, open: 2,
      },
      verification_completion: {
        population: 'plan cycles whose required work was completed in the period',
        numerator: 5, denominator: 7, rate: 0.7143, improved: 2, unchanged: 1, worsened: 2, not_conclusive: 2, improved_rate: 0.2857,
      },
      plan_closure: {
        population: 'agronomy plans drafted in the period and not superseded',
        numerator: 1, denominator: 10, rate: 0.1, closed_improved: 1, closed_without_improvement: 0, cancelled: 0, open: 9,
      },
    },
    breakdowns: {
      enterprises: [{
        ...breakdownMetrics({
          monitored: 13, active: 15,
          states: {
            needs_inspection: 3, inspection_active: 1, awaiting_review: 1, awaiting_decision: 1, plan_active: 2, work_active: 1,
            awaiting_satellite_verification: 1, verification_blocked: 1, improved_awaiting_closure: 1, not_improved: 2, reopened: 1,
          },
          overdueItems: 1,
          period: { inspections_opened: 15, resolved_cycles: 6, improved: 2, unchanged: 1, worsened: 1, unverified: 2, reopen_events: 2 },
        }),
        enterprise_id: 7,
        enterprise_name: 'Альфа Агро',
      }],
      current_crops: [
        { ...breakdownMetrics({ monitored: 5, active: 6, states: { needs_inspection: 1, work_active: 1 } }), current_crop_type_id: 302, current_crop_name: 'Пшеница' },
        { ...breakdownMetrics({ monitored: 7, active: 8, states: { needs_inspection: 2 } }), current_crop_type_id: 301, current_crop_name: 'Хлопок' },
        { ...breakdownMetrics({ monitored: 1, active: 1 }), current_crop_type_id: null, current_crop_name: null },
      ],
      periods: weekBuckets(),
      fields: { items: fieldRows(14), total: 14, limit: 50, offset: 0 },
    },
    limitations: [
      'Current-state sections describe the lifecycle at generated_at and are not narrowed by the period; windowed sections count facts whose documented anchor timestamp falls inside the period.',
      'Missing, cloud-, quality- or provider-blocked, too-early and inconclusive verifications are never counted as outcomes.',
    ],
  };
}

/** Every count zero: a scope with fields but no activity. */
export function quietSnapshot() {
  const snapshot = managerSnapshot();
  const zero = (value) => {
    if (Array.isArray(value)) return value.map(zero);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, zero(item)]));
    return typeof value === 'number' ? 0 : value;
  };
  const quiet = {
    ...snapshot,
    coverage: { ...zero(snapshot.coverage), fields_in_scope: 4, monitored_fields: 4, ndvi_freshness: { ...zero(snapshot.coverage.ndvi_freshness), fresh: 4 } },
    current: { ...zero(snapshot.current), as_of: snapshot.current.as_of },
    period_activity: zero(snapshot.period_activity),
    outcomes: zero(snapshot.outcomes),
    completion: Object.fromEntries(Object.entries(snapshot.completion).map(([key, value]) => [key, {
      ...zero(value), population: value.population, rate: null, ...(key === 'verification_completion' ? { improved_rate: null } : {}),
    }])),
    cycle_times: {
      ...snapshot.cycle_times,
      metrics: Object.fromEntries(Object.entries(snapshot.cycle_times.metrics).map(([key, value]) => [key, {
        ...value, sample_count: 0, median_hours: null, p90_hours: null, status: 'no_samples',
      }])),
    },
    breakdowns: {
      enterprises: [{ ...breakdownMetrics({ monitored: 4 }), enterprise_id: 7, enterprise_name: 'Альфа Агро' }],
      current_crops: [{ ...breakdownMetrics({ monitored: 4 }), current_crop_type_id: 301, current_crop_name: 'Хлопок' }],
      periods: zero(snapshot.breakdowns.periods).map((bucket, index) => ({
        ...bucket, bucket_start: snapshot.breakdowns.periods[index].bucket_start, bucket_end: snapshot.breakdowns.periods[index].bucket_end,
      })),
      fields: { items: fieldRows(4).map((row) => ({ ...row, ...breakdownMetrics() })), total: 4, limit: 50, offset: 0 },
    },
  };
  return clone(quiet);
}

/** No field matches the scope at all (for example an unused current crop). */
export function emptyScopeSnapshot(scope = {}) {
  const snapshot = quietSnapshot();
  snapshot.scope = { ...snapshot.scope, ...scope };
  snapshot.coverage = { ...snapshot.coverage, fields_in_scope: 0, monitored_fields: 0, ndvi_freshness: { ...snapshot.coverage.ndvi_freshness, fresh: 0 } };
  snapshot.breakdowns = { ...snapshot.breakdowns, enterprises: [], current_crops: [], fields: { items: [], total: 0, limit: 50, offset: 0 } };
  return snapshot;
}

/**
 * The admin snapshot for the given query: global unless narrowed. Distinct
 * totals per enterprise let a test prove which response is on screen.
 */
export function adminSnapshot(query = {}) {
  const snapshot = managerSnapshot();
  const enterpriseId = query.enterprise_id ? Number(query.enterprise_id) : null;
  const fieldId = query.field_id ? Number(query.field_id) : null;
  const cropId = query.current_crop_type_id ? Number(query.current_crop_type_id) : null;
  const offset = Number(query.field_offset || 0);
  const limit = Number(query.field_limit || 50);
  snapshot.scope = { role: 'admin', authorization: 'global', enterprise_id: enterpriseId, field_id: fieldId, current_crop_type_id: cropId };
  const enterprises = ENTERPRISES.filter((item) => !enterpriseId || item.id === enterpriseId);
  snapshot.breakdowns.enterprises = enterprises.map((item) => ({
    ...breakdownMetrics({ monitored: 40, active: item.id * 3, overdueCases: item.id === 8 ? 2 : 0 }),
    enterprise_id: item.id,
    enterprise_name: item.name,
  }));
  snapshot.current.active_problems.total = enterpriseId ? enterpriseId * 3 : 72;
  // Keep the state map summing to the total.
  snapshot.current.by_remediation_status.needs_inspection.total = snapshot.current.active_problems.total - 12;
  snapshot.current.by_remediation_status.needs_inspection.inspections = snapshot.current.active_problems.total - 14;
  const total = fieldId ? 1 : enterpriseId ? 40 : 120;
  const all = fieldRows(total, enterprises.length === 1 ? { enterpriseId: enterprises[0].id, enterpriseName: enterprises[0].name } : {});
  if (fieldId) all[0] = { ...all[0], field_id: fieldId, field_name: `Поле #${fieldId}` };
  snapshot.breakdowns.fields = { items: all.slice(offset, offset + limit), total, limit, offset };
  snapshot.breakdowns.current_crops = snapshot.breakdowns.current_crops.filter((row) => !cropId || row.current_crop_type_id === cropId);
  snapshot.coverage.fields_in_scope = total;
  if (query.date_from && query.date_to) {
    snapshot.period.requested = { date_from: query.date_from, date_to: query.date_to };
    const days = Math.round((Date.parse(`${query.date_to}T00:00:00Z`) - Date.parse(`${query.date_from}T00:00:00Z`)) / 86400000) + 1;
    snapshot.period.effective = { ...snapshot.period.effective, date_from: query.date_from, date_to: query.date_to, days };
  }
  if (query.granularity) snapshot.period.effective.granularity = query.granularity;
  return clone(snapshot);
}

/** Operational Center filter options (TASK_221 shape) of one enterprise. */
export function filterOptions(enterpriseId) {
  const enterprise = ENTERPRISES.find((item) => item.id === enterpriseId) || ENTERPRISES[0];
  return {
    enterprises: [{ id: enterprise.id, label: enterprise.name, enterprise_id: null }],
    fields: Array.from({ length: 6 }, (_, index) => ({ id: enterprise.id * 100 + index, label: `${enterprise.name} · поле ${index + 1}`, enterprise_id: enterprise.id })),
    crops: CROPS.map((crop) => ({ id: crop.id, label: crop.name, enterprise_id: null })),
    assignees: [],
  };
}
