// Deterministic TASK_225-contract API fixture for the TASK_226 browser
// qualification. Every request is recorded; retired TASK_225 write endpoints
// answer 410 exactly like the accepted backend, so any call to them is visible.
export const PASSWORD = 'task226-fixture-password';
export const USERS = Object.freeze({
  'manager@agrosat.test': { id: 11, full_name: 'Менеджер Тест', email: 'manager@agrosat.test', role: 'manager', enterprise_id: 7, is_active: true },
  'agro-a@agrosat.test': { id: 21, full_name: 'Агроном А', email: 'agro-a@agrosat.test', role: 'agronomist', enterprise_id: 7, is_active: true },
  'agro-b@agrosat.test': { id: 22, full_name: 'Агроном Б', email: 'agro-b@agrosat.test', role: 'agronomist', enterprise_id: 7, is_active: true },
  'viewer@agrosat.test': { id: 31, full_name: 'Наблюдатель', email: 'viewer@agrosat.test', role: 'viewer', enterprise_id: 7, is_active: true },
});

const RETIRED = [
  /^(POST|PUT|PATCH|DELETE) \/api\/field-inspections(\/|$)/,
  /^[A-Z]+ \/api\/operational-actions(\/|$)/,
  /^[A-Z]+ \/api\/verification-requests(\/|$)/,
  /^POST \/api\/anomaly-inspections\/[^/]+\/actions$/,
  /^POST \/api\/anomaly-inspections\/actions\//,
  /^POST \/api\/ndvi\/[^/]+\/refresh$/,
];

const FIELD_POLYGON = { type: 'Polygon', coordinates: [[[64.4, 39.8], [64.41, 39.8], [64.41, 39.81], [64.4, 39.81], [64.4, 39.8]]] };
const now = () => new Date().toISOString();

function queueItem(overrides) {
  return {
    case_key: 'inspection:100', source: 'inspection', source_id: '100', enterprise_id: 7, enterprise_name: 'Тестовое предприятие',
    field_id: 7, field_name: 'Поле 7', crop_type_id: null, crop_name: 'Хлопок', title: 'Фикстура', priority: 'normal',
    operational_status: 'awaiting_inspection', remediation_status: 'needs_inspection', assignee_id: null, assignee_name: null,
    due_at: null, is_overdue: false, blocked: false, awaiting_verification: false, external_state: null, source_time: now(),
    priority_reasons: ['accepted_source_state'], unread_notifications: 0, active_notifications: 0, inspection_id: 100,
    plan_id: null, candidate_id: null, alert_id: null, provenance: {},
    ...overrides,
  };
}

export const OPERATIONAL_CASES = [
  queueItem({ case_key: 'inspection:101', source_id: '101', inspection_id: 101, plan_id: 7, title: 'Закрыто без улучшения', priority: 'urgent', operational_status: 'closed_without_improvement', remediation_status: 'closed_without_improvement', provenance: { plan_status: 'closed', verification_status: 'WORSENED' } }),
  queueItem({ case_key: 'inspection:102', source_id: '102', inspection_id: 102, plan_id: 8, title: 'Улучшение подтверждено', operational_status: 'improved_closed', remediation_status: 'improved_closed', provenance: { plan_status: 'closed', verification_status: 'IMPROVED' } }),
  queueItem({ case_key: 'inspection:103', source_id: '103', inspection_id: 103, plan_id: 9, title: 'Ухудшение после работ', operational_status: 'awaiting_verification', remediation_status: 'not_improved', provenance: { plan_status: 'pending_verification', verification_status: 'WORSENED' } }),
  queueItem({ case_key: 'inspection:104', source_id: '104', inspection_id: 104, plan_id: 10, title: 'Без изменений после работ', operational_status: 'awaiting_verification', remediation_status: 'not_improved', provenance: { plan_status: 'pending_verification', verification_status: 'NO_MATERIAL_CHANGE' } }),
  queueItem({ case_key: 'inspection:105', source_id: '105', inspection_id: 105, plan_id: 11, title: 'Доработка плана', operational_status: 'awaiting_work', remediation_status: 'reopened', provenance: { plan_status: 'rework' } }),
  queueItem({ case_key: 'inspection:106', source_id: '106', inspection_id: 106, plan_id: 12, title: 'Облачность', operational_status: 'awaiting_verification', remediation_status: 'verification_blocked', blocked: true, provenance: { plan_status: 'pending_verification', verification_status: 'CLOUD_BLOCKED' } }),
  queueItem({ case_key: 'candidate:9', source: 'candidate', source_id: '9', inspection_id: null, title: 'Спутниковая аномалия NDVI', priority: 'high', operational_status: 'needs_review', remediation_status: 'needs_inspection' }),
  queueItem({ case_key: 'freshness:7:ndvi', source: 'freshness', source_id: '7:ndvi', inspection_id: null, title: 'NDVI: состояние спутниковых данных', priority: 'warning', operational_status: 'stale', remediation_status: 'data_unavailable' }),
];

export const OPERATIONAL_SUMMARY = {
  as_of: now(), active_situations: 6, overdue_work: 0, blocked_or_external_unavailable: 1, awaiting_field_inspection: 1,
  awaiting_work: 1, awaiting_evidence: 0, awaiting_satellite_verification: 3, improved_or_closed_recent: 1,
  closed_without_improvement_recent: 4, not_improved: 3, reopened: 2, verification_blocked: 5,
};

function alertsPage(count) {
  return Array.from({ length: count }, (_, index) => ({
    id: 900 + index, field_id: 7, alert_type: index < 3 ? 'ndvi_drop' : 'ndvi_low', severity: index < 3 ? 'critical' : 'warning',
    title: `Предупреждение ${index + 1}`, description: 'Фикстура', recommendation: null, triggered_value: 0.2,
    threshold_value: 0.3, triggered_at: now(), acknowledged_at: null, acknowledged_by_id: null, is_active: true,
    field_name: 'Поле 7', enterprise_name: 'Тестовое предприятие', captured_date: '2026-09-20', cloud_cover_pct: 5, snapshot_ndvi: 0.2,
  }));
}

export class MockApi {
  constructor() {
    this.tokens = new Map();
    this.revoked = new Set();
    this.requests = [];
    this.overrides = new Map();
    this.loginUnavailable = false;
    this.inspectionAssignee = 21;
    this.findings = [];
    this.savedFindings = new Map();
    this.createdInspections = [];
    this.irrigationActive = null;
    this.workCreated = [];
    this.tokenCounter = 0;
  }

  retiredCalls() {
    return this.requests.filter((request) => RETIRED.some((pattern) => pattern.test(`${request.method} ${request.path}`)));
  }

  writes(pathPrefix) {
    return this.requests.filter((request) => request.method !== 'GET' && request.path.startsWith(pathPrefix));
  }

  override(method, path, handler) { this.overrides.set(`${method} ${path}`, handler); }
  clearOverride(method, path) { this.overrides.delete(`${method} ${path}`); }

  userFor(headers) {
    const authorization = headers.authorization || '';
    const token = authorization.startsWith('Bearer ') ? authorization.slice(7) : null;
    if (!token || this.revoked.has(token) || !this.tokens.has(token)) return { token, user: null };
    return { token, user: USERS[this.tokens.get(token)] };
  }

  handle(request) {
    const url = new URL(request.url());
    const method = request.method();
    const headers = request.headers();
    const path = url.pathname;
    let body = null;
    const raw = request.postData();
    if (raw) {
      try { body = JSON.parse(raw); } catch { body = raw; }
    }
    const { token, user } = this.userFor(headers);
    const entry = { method, path, search: url.search, body, token, userId: user?.id || null, idempotencyKey: headers['idempotency-key'] || null, at: Date.now() };
    this.requests.push(entry);
    const json = (payload, status = 200) => ({ status, body: payload });

    const override = this.overrides.get(`${method} ${path}`);
    if (override) {
      const result = override(entry, user);
      if (result) return result;
    }
    if (RETIRED.some((pattern) => pattern.test(`${method} ${path}`))) {
      return json({ detail: { code: 'lifecycle_endpoint_retired', endpoint: path } }, 410);
    }

    if (path === '/api/auth/login' && method === 'POST') {
      if (this.loginUnavailable) return json({ detail: 'unavailable' }, 503);
      const form = new URLSearchParams(raw || '');
      const email = form.get('username');
      if (!USERS[email] || form.get('password') !== PASSWORD) return json({ detail: 'Неверный email или пароль' }, 401);
      const issued = `token-${USERS[email].id}-${++this.tokenCounter}`;
      this.tokens.set(issued, email);
      return json({ access_token: issued, token_type: 'bearer' });
    }
    if (!user) return json({ detail: 'Не удалось подтвердить учетные данные' }, 401);
    if (path === '/api/auth/me') return json(user);

    if (path === '/api/enterprises/' || path === '/api/enterprises') return json([{ id: 7, name: 'Тестовое предприятие', region: 'Бухара', fields_count: 2 }]);
    if (path === '/api/enterprises/7') {
      return json({ id: 7, name: 'Тестовое предприятие', code: 'T7', region: 'Бухара', total_area_ha: 120, field_count: 1, fields: [
        { id: 7, name: 'Поле 7', code: 'F7', area_ha: 60, current_crop: 'Хлопок', current_ndvi: 0.41, last_ndvi_date: '2026-09-20', irrigation_type: 'drip', active_alerts: 2, alert_severity: 'critical' },
      ] });
    }
    if (path === '/api/dashboard/summary') {
      return json({ total_fields: 275, total_area_ha: 12345.6, avg_ndvi: 0.512, active_alerts: 57, critical_alerts: 12, warning_alerts: 30, fields_no_data: 3, last_updated: '2026-09-24' });
    }
    if (path === '/api/alerts/') return json(alertsPage(Math.min(Number(url.searchParams.get('limit') || 100), 20)));
    if (/^\/api\/alerts\/\d+$/.test(path)) return json([]);
    if (path === '/api/satellite-indices/coverage') {
      return json({ filters: {}, summary: { fields_total: 275, fields_with_any_data: 270, fields_without_data: 5, fields_with_all_requested_indices: 260, fields_with_partial_indices: 10, fields_stale: 4, index_summary: {}, latest_captured_date: '2026-09-20', record_count_total: 13986 }, fields: [] });
    }
    if (path === '/api/field-attention/queue') {
      return json({
        generated_at: now(), date_to: '2026-09-24', lookback_days: 180,
        summary: { total: 9, critical: 1, high: 3, medium: 5 },
        items: [{
          field: { id: 11, name: 'Поле внимания', enterprise_id: 7, enterprise_name: 'Тестовое предприятие', crop_name: 'Хлопок', crop_type_id: 2, season_year: 2026 },
          priority: 'critical', attention_score: 91,
          reasons: [{ code: 'ndvi_drop', label: 'Падение NDVI' }, { code: 'weather_water_deficit', label: 'Дефицит влаги' }],
          recommended_checks: ['Проверить полив'], spectral_summary: { latest_observation_date: '2026-09-20', data_status: 'ok' }, alert_summary: {},
        }],
      });
    }
    if (path === '/api/anomaly-inspections/queue') {
      const items = [this.inspection(41, user)];
      return json({ generated_at: now(), summary: { total: 7, open: 6, overdue: 1, awaiting_review: 2, active_actions: 3, verification_due: 1 }, limit: 50, offset: 0, items });
    }
    if (path === '/api/anomaly-inspections/assignees') {
      return json([{ id: 21, full_name: 'Агроном А', email: 'agro-a@agrosat.test', enterprise_id: 7 }, { id: 22, full_name: 'Агроном Б', email: 'agro-b@agrosat.test', enterprise_id: 7 }]);
    }
    if (path === '/api/anomaly-inspections' && method === 'POST') {
      const id = 500 + this.createdInspections.length + 1;
      this.createdInspections.push({ id, body, idempotencyKey: entry.idempotencyKey, userId: user.id });
      if (body?.reason?.startsWith('Контекст орошения')) this.irrigationActive = { id, status: body.assigned_to_id ? 'assigned' : 'new', source: 'manual', source_priority: body.priority, source_observation_date: null, source_reason_codes: [] };
      return json({ created: true, inspection: { ...this.inspection(id, user), source: { kind: 'manual', reason: body?.reason } } }, 201);
    }
    const inspectionMatch = path.match(/^\/api\/anomaly-inspections\/(\d+)$/);
    if (inspectionMatch && method === 'GET') return json(this.inspection(Number(inspectionMatch[1]), user));
    const findingMatch = path.match(/^\/api\/anomaly-inspections\/(\d+)\/finding$/);
    if (findingMatch && method === 'PUT') {
      this.findings.push({ inspectionId: Number(findingMatch[1]), body, userId: user.id });
      this.savedFindings.set(Number(findingMatch[1]), body);
      return json({ inspection: this.inspection(Number(findingMatch[1]), user) });
    }

    if (path === '/api/irrigation-context/fields/1') {
      return json({
        generated_at: now(), field: { id: 1, enterprise_id: 7, name: 'Поле 1', irrigation_type: 'drip' },
        weather: { status: 'available', provider: 'open_meteo', provenance: { provider: 'open_meteo', fetched_at: now(), provider_observed_at: now(), timezone: 'Asia/Tashkent' }, current: { temperature: 31, humidity: 30, wind_speed: 9, precipitation: 0 }, forecast: [] },
        events: [], event_limit: 20, active_inspection: this.irrigationActive,
        supported_reason_codes: ['water_stress_suspicion'], causality_limitation: 'Фикстура.',
      });
    }
    if (path === '/api/fields/1') {
      return json({ type: 'Feature', geometry: FIELD_POLYGON, properties: { id: 1, name: 'Поле 1', code: 'F1', enterprise_id: 7, enterprise_name: 'Тестовое предприятие', area_ha: 42.5, irrigation_type: 'drip', current_crop: 'Хлопок' } });
    }

    if (path === '/api/operational-center/queue') return json({ as_of: now(), items: OPERATIONAL_CASES, total: OPERATIONAL_CASES.length, limit: 50, offset: 0 });
    if (path === '/api/operational-center/summary') return json({ ...OPERATIONAL_SUMMARY, as_of: now() });
    if (path === '/api/operational-center/filter-options') return json({ enterprises: [{ id: 7, label: 'Тестовое предприятие' }], fields: [{ id: 7, label: 'Поле 7', enterprise_id: 7 }], crops: [], assignees: [] });
    const caseMatch = path.match(/^\/api\/operational-center\/cases\/(.+)$/);
    if (caseMatch) {
      const key = decodeURIComponent(caseMatch[1]);
      const item = OPERATIONAL_CASES.find((value) => value.case_key === key);
      if (!item) return json({ detail: 'Case not found' }, 404);
      return json({
        as_of: now(), case: item, field: null, source_snapshot: { source_kind: 'manual' }, inspection: null, agronomy_plan: null,
        work_items: [{ id: 1, instruction: 'Полив по графику', status: 'completed', assignee_name: 'Агроном А', due_at: now(), result_note: 'Выполнено' }],
        evidence: [], verifications: [{ id: 1, cycle: 1, status: item.provenance?.verification_status || 'PENDING_DATA', created_at: now() }],
        weather: { status: 'unavailable', provider: 'open_meteo', reason: 'missing_field' },
        telematics: { status: 'unsupported', provider: 'wialon', reason: 'mapping_unavailable', units: [] },
        notifications: [], timeline: [], links: {}, causality_limitation: 'Контекст не доказывает причинность.',
      });
    }

    const planMatch = path.match(/^\/api\/agronomy-plans\/(\d+)$/);
    if (path === '/api/agronomy-plans/queue') return json({ items: [this.plan(5, 'list')], total: 1, limit: 50, offset: 0 });
    if (path === '/api/agronomy-plans/summary') return json({ open: 1, overdue: 0, pending_verification: 0, improved: 0, ineffective: 0 });
    if (planMatch && method === 'GET') return json(this.plan(Number(planMatch[1]), 'detail'));
    const workMatch = path.match(/^\/api\/agronomy-plans\/(\d+)\/work$/);
    if (workMatch && method === 'POST') {
      this.workCreated.push({ planId: Number(workMatch[1]), body, idempotencyKey: entry.idempotencyKey });
      return json({ plan_id: Number(workMatch[1]), version: 3, status: 'draft', item_id: 1, item_version: 1 }, 201);
    }

    if (method === 'GET') return json({ detail: 'Fixture route not found' }, 404);
    return json({ detail: 'Fixture write not allowed' }, 405);
  }

  inspection(id, user) {
    const assignee = id === 41 ? (user?.role === 'agronomist' ? user.id : this.inspectionAssignee) : 21;
    const saved = this.savedFindings.get(id);
    const finding = saved ? {
      actual_inspected_at: saved.inspected_at, cause_code: saved.cause, cause_details: saved.other_explanation, severity: saved.severity,
      affected_area_ha: saved.affected_area_ha, affected_area_pct: saved.affected_area_pct, observations: saved.observations,
      recommended_action: saved.recommended_action, gps_point: null, gps_accuracy_m: null,
    } : null;
    return {
      id, enterprise_id: 7, enterprise_name: 'Тестовое предприятие', field_id: 7, field_name: 'Поле 7', created_by_id: 11,
      creator_name: 'Менеджер Тест', assigned_to_id: assignee, assignee_name: 'Агроном', status: id === 41 ? 'in_progress' : 'assigned',
      priority: 'high', due_at: '2026-09-30T12:00:00+00:00', is_overdue: false, version: 4,
      source: { kind: 'manual', alert_id: null, provider: null, item_id: null, acquired_at: null, index_name: null, sampled_value: null, comparison_value: null, delta: null, geometry_hash: null, point: null, zone: null, reason: 'Проверить состояние поля' },
      review: { reviewed_by_id: null, reviewer_name: null, reviewed_at: null, reason: null },
      created_at: now(), updated_at: now(), started_at: now(), submitted_at: null, confirmed_at: null, rejected_at: null, cancelled_at: null,
      cancellation_reason: null, follow_up_of_id: null, field_geometry: FIELD_POLYGON, finding, photos: [], actions: [], timeline: [],
    };
  }

  plan(id, shape) {
    const base = { id, inspection_id: 41, enterprise_id: 7, field_id: 7, field_name: 'Поле 7', priority: 'high', status: 'draft', version: 2, verification_status: null, created_at: now(), decision: 'Проверить полив', enterprise_name: 'Тестовое предприятие', source_kind: 'manual', due_at: null };
    if (shape === 'list') return base;
    return {
      ...base, policy_version: 'r3-f-v1', objective: 'Цель', expected_outcome: 'Ожидаемый результат',
      recommendation: { explanation: 'Рекомендация фикстуры', confidence: 'low', limitations: ['Нужна проверка агронома.'] },
      field_geometry: FIELD_POLYGON, work_items: [], photos: [], timeline: [], inspection_timeline: [], verifications: [],
      assignees: [{ id: 21, full_name: 'Агроном А', role: 'agronomist' }, { id: 11, full_name: 'Менеджер Тест', role: 'manager' }],
      inspection: this.inspection(41, null), finding: null,
    };
  }
}
