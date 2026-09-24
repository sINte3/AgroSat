function finiteCount(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

/**
 * Alert KPIs come from the server-side dashboard summary (GET /api/dashboard/summary).
 * The alert list is a bounded "top alerts" page and is never counted as the
 * alert population. No "info" total is derived: the summary does not publish
 * one and the remainder of active - critical - warning is not guaranteed to be
 * info severity.
 */
export function dashboardAlertTotals(summary) {
  if (!summary) return null;
  const active = finiteCount(summary.active_alerts);
  if (active === null) return null;
  return {
    active,
    critical: finiteCount(summary.critical_alerts),
    warning: finiteCount(summary.warning_alerts),
  };
}

export { finiteCount };
