const FULL_NAVIGATION_KEYS = Object.freeze([
  'dashboard',
  'fields',
  'field-attention',
  'field-inspections',
  'agronomy-plans',
  'alerts',
  'enterprises',
  'reports',
]);

const ADMIN_NAVIGATION_KEYS = Object.freeze([
  'dashboard', 'fields', 'monitoring', 'field-attention',
  'field-inspections', 'alerts', 'enterprises', 'reports',
  'agronomy-plans',
]);

const AGRONOMIST_NAVIGATION_KEYS = Object.freeze([
  'field-inspections',
  'agronomy-plans',
  'field-attention',
  'fields',
]);

const FULL_VIEW_KEYS = new Set([
  ...FULL_NAVIGATION_KEYS,
  'field-detail',
  'field-analytics',
  'field-inspection-detail',
  'enterprise-detail',
]);

const ADMIN_VIEW_KEYS = new Set([...FULL_VIEW_KEYS, 'monitoring']);

const AGRONOMIST_VIEW_KEYS = new Set([
  'agronomy-plans',
  'fields',
  'field-detail',
  'field-analytics',
  'field-attention',
  'field-inspections',
  'field-inspection-detail',
]);

const NAVIGATION_LABELS = Object.freeze({
  'agronomy-plans': 'Меры и контроль',
  monitoring: 'Мониторинг',
  dashboard: 'Сегодня',
  fields: 'Поля',
  'field-attention': 'Внимание',
  'field-inspections': 'Осмотры',
  alerts: 'Предупреждения',
  enterprises: 'Предприятия',
  reports: 'Отчёты',
});

export function getRoleDefaultPath(role) {
  if (role === 'agronomist') return '/inspections';
  if (role === 'admin' || role === 'manager' || role === 'viewer') return '/dashboard';
  return '/unauthorized';
}

export function isViewAllowedForRole(role, view) {
  if (role === 'admin') return ADMIN_VIEW_KEYS.has(view);
  if (role === 'agronomist') return AGRONOMIST_VIEW_KEYS.has(view);
  if (role === 'manager' || role === 'viewer') return FULL_VIEW_KEYS.has(view);
  return false;
}

export function getNavigationKeysForRole(role) {
  if (role === 'admin') return [...ADMIN_NAVIGATION_KEYS];
  if (role === 'agronomist') return [...AGRONOMIST_NAVIGATION_KEYS];
  if (role === 'manager' || role === 'viewer') return [...FULL_NAVIGATION_KEYS];
  return [];
}

export function getNavigationLabelForRole(role, key) {
  if (role === 'agronomist' && key === 'field-inspections') return 'Мои осмотры';
  return NAVIGATION_LABELS[key] || '';
}
