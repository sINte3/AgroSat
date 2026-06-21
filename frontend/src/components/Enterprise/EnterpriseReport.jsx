/**
 * EnterpriseReport.jsx — HTML-отчёт для печати (Print-friendly)
 *
 * Props:
 *   enterprise — объект предприятия
 *   fields — массив полей
 *   alerts — массив алертов
 *   ndviData — объект { [fieldId]: ndviArray }
 *
 * Секции:
 *   1. Обложка
 *   2. Оглавление
 *   3. Executive Summary
 *   4. Детали по полям
 *   5. Сводная таблица алертов
 *   6. Контакты
 *
 * Вызов: window.print() в EnterpriseDetailPage
 */

import { useMemo } from 'react';

// ─── NDVI цвет ──────────────────────────────────────────────────────────────
const getNDVIColor = (ndvi) => {
  if (ndvi == null) return '#374151';
  if (ndvi < 0.2) return '#8B0000';
  if (ndvi < 0.35) return '#FF4500';
  if (ndvi < 0.5) return '#FFD700';
  if (ndvi < 0.65) return '#9ACD32';
  if (ndvi < 0.8) return '#228B22';
  return '#006400';
};

const getNDVIStatus = (ndvi) => {
  if (ndvi == null) return 'Нет данных';
  if (ndvi < 0.2) return 'Критический';
  if (ndvi < 0.35) return 'Плохой';
  if (ndvi < 0.5) return 'Удовлетворительный';
  if (ndvi < 0.65) return 'Хороший';
  return 'Отличный';
};

// ─── SVG мини-график NDVI (для печати) ──────────────────────────────────────
function NdviMiniChart({ data, width = 200, height = 80 }) {
  if (!data || data.length < 2) {
    return (
      <div style={{ width, height, background: '#f0f0f0', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: '#999' }}>
        Нет данных
      </div>
    );
  }

  // Нормализация
  const pts = data
    .filter(d => d.mean_ndvi != null)
    .sort((a, b) => new Date(a.captured_date) - new Date(b.captured_date));

  if (pts.length < 2) {
    return (
      <div style={{ width, height, background: '#f0f0f0', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, color: '#999' }}>
        Нет данных
      </div>
    );
  }

  const minNdvi = Math.max(0, Math.min(...pts.map(p => p.mean_ndvi)) - 0.05);
  const maxNdvi = Math.min(1, Math.max(...pts.map(p => p.mean_ndvi)) + 0.05);
  const range = maxNdvi - minNdvi || 0.1;

  const padding = { top: 6, bottom: 6, left: 4, right: 4 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  const toX = (i) => padding.left + (i / (pts.length - 1)) * plotW;
  const toY = (v) => padding.top + (1 - (v - minNdvi) / range) * plotH;

  const linePath = pts.map((p, i) =>
    `${i === 0 ? 'M' : 'L'}${toX(i)},${toY(p.mean_ndvi)}`
  ).join(' ');

  const areaPath = `${linePath} L${toX(pts.length - 1)},${toY(minNdvi)} L${toX(0)},${toY(minNdvi)} Z`;

  return (
    <svg width={width} height={height} style={{ borderRadius: 4, background: '#fafafa' }}>
      <defs>
        <linearGradient id={`grad-${pts[0]?.captured_date || 'g'}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#22c55e" stopOpacity={0.3} />
          <stop offset="100%" stopColor="#22c55e" stopOpacity={0.05} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#grad-${pts[0]?.captured_date || 'g'})`} />
      <path d={linePath} fill="none" stroke="#22c55e" strokeWidth={2} />
      {pts.map((p, i) => (
        <circle key={i} cx={toX(i)} cy={toY(p.mean_ndvi)} r={2} fill="#22c55e" />
      ))}
    </svg>
  );
}

// ─── Main Report Component ──────────────────────────────────────────────────
export default function EnterpriseReport({ enterprise, fields, alerts, ndviData }) {
  const reportDate = useMemo(() => {
    return new Date().toLocaleDateString('ru-RU', {
      day: 'numeric',
      month: 'long',
      year: 'numeric',
    });
  }, []);

  // Агрегированные данные
  const summary = useMemo(() => {
    if (!fields || fields.length === 0) return null;

    const ndviValues = fields
      .map(f => f.current_ndvi)
      .filter(v => v != null && !isNaN(v));
    const avgNdvi = ndviValues.length > 0
      ? ndviValues.reduce((a, b) => a + b, 0) / ndviValues.length
      : null;

    const criticalAlerts = alerts ? alerts.filter(a => a.severity === 'critical') : [];
    const warningAlerts = alerts ? alerts.filter(a => a.severity === 'warning') : [];
    const fieldsWithAlerts = fields.filter(f => f.active_alerts > 0);
    const ndviDataCount = ndviData ? Object.keys(ndviData).length : 0;

    return {
      totalFields: fields.length,
      avgNdvi,
      totalArea: fields.reduce((s, f) => s + (f.area_ha || 0), 0),
      criticalAlerts: criticalAlerts.length,
      warningAlerts: warningAlerts.length,
      fieldsWithAlerts: fieldsWithAlerts.length,
      ndviDataCount,
      fieldsWithNoData: fields.filter(f => f.current_ndvi == null).length,
    };
  }, [fields, alerts, ndviData]);

  if (!enterprise || !fields) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: '#999' }}>
        Недостаточно данных для формирования отчёта
      </div>
    );
  }

  // Группируем критические алерты по полям
  const criticalAlertsByField = useMemo(() => {
    if (!alerts) return {};
    const byField = {};
    alerts
      .filter(a => a.severity === 'critical')
      .forEach(a => {
        if (!byField[a.field_id]) byField[a.field_id] = [];
        byField[a.field_id].push(a);
      });
    return byField;
  }, [alerts]);

  return (
    <div className="report-container">
      {/* ======== СТИЛИ ДЛЯ ПЕЧАТИ ======== */}
      <style>{`
        @media print {
          @page {
            margin: 15mm 18mm;
            size: A4;
          }
          body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
          .report-container {
            font-family: Arial, Helvetica, sans-serif !important;
            color: #1a1a1a !important;
            background: #ffffff !important;
          }
          .report-container * {
            color: #1a1a1a !important;
          }
          .no-print { display: none !important; }
          .page-break { page-break-after: always; }
          .report-section { page-break-inside: avoid; }
          .report-field-row { page-break-inside: avoid; }
          table { page-break-inside: auto; }
          tr { page-break-inside: avoid; page-break-after: auto; }
          thead { display: table-header-group; }
          tfoot { display: table-footer-group; }
        }
        @media screen {
          .report-container {
            font-family: Arial, Helvetica, sans-serif;
            color: #1a1a1a;
            background: #ffffff;
            max-width: 210mm;
            margin: 0 auto;
            padding: 20px;
          }
          .report-cover {
            text-align: center;
            padding: 60px 40px;
            border: 1px solid #e0e0e0;
            border-radius: 12px;
            margin-bottom: 30px;
          }
        }
      `}</style>

      {/* ======== 1. ОБЛОЖКА ======== */}
      <div className="report-cover page-break" style={{ textAlign: 'center', padding: '80px 40px 60px', position: 'relative' }}>
        <div style={{ fontSize: 14, color: '#22c55e', fontWeight: 700, letterSpacing: 2, marginBottom: 8 }}>
          AGROSAT
        </div>
        <h1 style={{ fontSize: 28, fontWeight: 700, margin: '20px 0 8px', color: '#1a1a1a' }}>
          {enterprise.name}
        </h1>
        <div style={{ fontSize: 14, color: '#666', marginBottom: 30 }}>
          Код: {enterprise.code} • Регион: {enterprise.region || '—'}
        </div>
        <div style={{
          width: 60, height: 3, background: '#22c55e',
          margin: '0 auto 30px', borderRadius: 2,
        }} />
        <div style={{ fontSize: 13, color: '#888', marginBottom: 6 }}>
          Отчёт по мониторингу полей
        </div>
        <div style={{ fontSize: 13, color: '#888' }}>
          Составлено: {reportDate}
        </div>
      </div>

      {/* ======== 2. ОГЛАВЛЕНИЕ ======== */}
      <div className="page-break" style={{ padding: '20px 0' }}>
        <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20, color: '#1a1a1a', borderBottom: '2px solid #22c55e', paddingBottom: 8 }}>
          Оглавление
        </h2>
        <ol style={{ fontSize: 14, lineHeight: 2.2, paddingLeft: 20 }}>
          <li>Обложка</li>
          <li>Оглавление</li>
          <li>Executive Summary</li>
          <li>Детали по полям ({fields.length} полей)</li>
          {alerts && alerts.filter(a => a.severity === 'critical').length > 0 && (
            <li>Сводная таблица алертов</li>
          )}
          <li>Контактная информация</li>
        </ol>
      </div>

      {/* ======== 3. EXECUTIVE SUMMARY ======== */}
      <div className="report-section page-break" style={{ padding: '20px 0' }}>
        <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20, color: '#1a1a1a', borderBottom: '2px solid #22c55e', paddingBottom: 8 }}>
          Executive Summary
        </h2>

        {summary && (
          <div style={{ marginBottom: 24 }}>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
              gap: 12,
              marginBottom: 20,
            }}>
              <KpiBox label="Всего полей" value={summary.totalFields} />
              <KpiBox
                label="Средний NDVI"
                value={summary.avgNdvi != null ? summary.avgNdvi.toFixed(4) : '—'}
                color={summary.avgNdvi != null ? getNDVIColor(summary.avgNdvi) : undefined}
              />
              <KpiBox label="Общая площадь" value={`${summary.totalArea.toFixed(1)} га`} />
              <KpiBox label="Критических алертов" value={summary.criticalAlerts} color={summary.criticalAlerts > 0 ? '#ef4444' : undefined} />
              <KpiBox label="Предупреждений" value={summary.warningAlerts} color={summary.warningAlerts > 0 ? '#f59e0b' : undefined} />
              <KpiBox label="Поля с алертами" value={summary.fieldsWithAlerts} color={summary.fieldsWithAlerts > 0 ? '#f59e0b' : undefined} />
            </div>

            <div style={{
              background: '#f9fafb',
              border: '1px solid #e5e7eb',
              borderRadius: 8,
              padding: '16px 20px',
              marginTop: 16,
            }}>
              <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 10, color: '#1a1a1a' }}>
                Ключевые выводы
              </h3>
              <ul style={{ fontSize: 13, lineHeight: 2, paddingLeft: 20, color: '#333' }}>
                {summary.totalFields > 0 && (
                  <li>Предприятие насчитывает <strong>{summary.totalFields} полей</strong> общей площадью <strong>{summary.totalArea.toFixed(1)} га</strong>.</li>
                )}
                {summary.avgNdvi != null && (
                  <li>
                    Средний NDVI по всем полям: <strong style={{ color: getNDVIColor(summary.avgNdvi) }}>{summary.avgNdvi.toFixed(4)}</strong> — {getNDVIStatus(summary.avgNdvi).toLowerCase()}.
                  </li>
                )}
                {summary.criticalAlerts > 0 && (
                  <li>Обнаружено <strong style={{ color: '#ef4444' }}>{summary.criticalAlerts} критических алертов</strong>, требующих немедленного внимания.</li>
                )}
                {summary.warningAlerts > 0 && (
                  <li>Зафиксировано <strong style={{ color: '#f59e0b' }}>{summary.warningAlerts} предупреждений</strong>.</li>
                )}
                {summary.fieldsWithNoData > 0 && (
                  <li><strong>{summary.fieldsWithNoData} полей</strong> не имеют данных NDVI — требуется проверка спутниковых снимков.</li>
                )}
              </ul>
            </div>
          </div>
        )}
      </div>

      {/* ======== 4. ДЕТАЛИ ПО ПОЛЯМ ======== */}
      <div className="report-section page-break" style={{ padding: '20px 0' }}>
        <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20, color: '#1a1a1a', borderBottom: '2px solid #22c55e', paddingBottom: 8 }}>
          Детали по полям
        </h2>

        {fields.length === 0 ? (
          <div style={{ color: '#666', fontSize: 13 }}>Нет полей для отображения</div>
        ) : (
          fields.map((field, idx) => {
            const fieldAlerts = alerts ? alerts.filter(a => a.field_id === field.id) : [];
            const fieldNdvi = ndviData ? ndviData[field.id] : null;
            const ndviColor = getNDVIColor(field.current_ndvi);

            return (
              <div
                key={field.id}
                className="report-field-row"
                style={{
                  padding: '14px 16px',
                  marginBottom: 10,
                  background: idx % 2 === 0 ? '#f9fafb' : '#ffffff',
                  border: '1px solid #e5e7eb',
                  borderRadius: 6,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <div style={{ fontSize: 15, fontWeight: 700, color: '#1a1a1a', marginBottom: 4 }}>
                      {field.name || `Поле #${field.id}`}
                    </div>
                    <div style={{ fontSize: 12, color: '#666', marginBottom: 8 }}>
                      Код: {field.code || '—'} • Площадь: {field.area_ha != null ? `${field.area_ha.toFixed(1)} га` : '—'}
                      {field.current_crop ? ` • Культура: ${field.current_crop}` : ''}
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                      <span style={{ fontSize: 13 }}>
                        NDVI: <strong style={{ color: ndviColor }}>
                          {field.current_ndvi != null ? field.current_ndvi.toFixed(4) : '—'}
                        </strong>
                      </span>
                      <span style={{
                        fontSize: 11,
                        padding: '1px 6px',
                        borderRadius: 3,
                        background: `${ndviColor}20`,
                        color: ndviColor,
                        fontWeight: 600,
                      }}>
                        {getNDVIStatus(field.current_ndvi)}
                      </span>
                    </div>

                    {fieldAlerts.length > 0 && (
                      <div style={{ marginTop: 6 }}>
                        {fieldAlerts.slice(0, 3).map(alert => (
                          <div
                            key={alert.id}
                            style={{
                              fontSize: 11,
                              color: alert.severity === 'critical' ? '#ef4444' : '#f59e0b',
                              padding: '2px 0',
                            }}
                          >
                            {alert.severity === 'critical' ? '🔴' : '🟡'} {alert.title}
                            {alert.recommendation && (
                              <span style={{ color: '#666', marginLeft: 4 }}>
                                — {alert.recommendation}
                              </span>
                            )}
                          </div>
                        ))}
                        {fieldAlerts.length > 3 && (
                          <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                            + ещё {fieldAlerts.length - 3} алертов
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Мини-график NDVI */}
                  <div style={{ flexShrink: 0 }}>
                    <NdviMiniChart data={fieldNdvi || []} width={200} height={80} />
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* ======== 5. СВОДНАЯ ТАБЛИЦА АЛЕРТОВ ======== */}
      {alerts && alerts.filter(a => a.severity === 'critical').length > 0 && (
        <div className="report-section page-break" style={{ padding: '20px 0' }}>
          <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 20, color: '#1a1a1a', borderBottom: '2px solid #22c55e', paddingBottom: 8 }}>
            Сводная таблица алертов
          </h2>

          <table style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: 12,
          }}>
            <thead>
              <tr style={{ background: '#f3f4f6', borderBottom: '2px solid #e5e7eb' }}>
                <th style={printTh}>Поле</th>
                <th style={printTh}>Тип алерта</th>
                <th style={printTh}>Значение</th>
                <th style={printTh}>Порог</th>
                <th style={printTh}>Рекомендация</th>
                <th style={printTh}>Дата</th>
              </tr>
            </thead>
            <tbody>
              {alerts
                .filter(a => a.severity === 'critical')
                .map((alert, idx) => {
                  const field = fields.find(f => f.id === alert.field_id);
                  return (
                    <tr key={alert.id} style={{ borderBottom: '1px solid #e5e7eb', background: idx % 2 === 0 ? '#ffffff' : '#f9fafb' }}>
                      <td style={printTd}>{field?.name || `#${alert.field_id}`}</td>
                      <td style={printTd}>
                        <span style={{ color: '#ef4444', fontWeight: 600 }}>{alert.alert_type}</span>
                      </td>
                      <td style={printTd}>{alert.triggered_value != null && Math.abs(alert.triggered_value) <= 1.0 ? alert.triggered_value.toFixed(4) : '—'}</td>
                      <td style={printTd}>{alert.threshold_value != null ? alert.threshold_value.toFixed(4) : '—'}</td>
                      <td style={printTd}>{alert.recommendation || '—'}</td>
                      <td style={printTd}>
                        {alert.triggered_at
                          ? new Date(alert.triggered_at).toLocaleDateString('ru-RU')
                          : '—'}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>

          {/* Сводка по не-критическим алертам */}
          {alerts.filter(a => a.severity === 'warning').length > 0 && (
            <div style={{ marginTop: 16, fontSize: 12, color: '#666' }}>
              Дополнительно: {alerts.filter(a => a.severity === 'warning').length} предупреждений
              (не требуют немедленного вмешательства)
            </div>
          )}
        </div>
      )}

      {/* ======== 6. КОНТАКТНАЯ ИНФОРМАЦИЯ ======== */}
      <div className="report-section" style={{ padding: '30px 0', marginTop: 20 }}>
        <div style={{
          borderTop: '2px solid #22c55e',
          paddingTop: 20,
          textAlign: 'center',
        }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: '#22c55e', marginBottom: 8 }}>
            AGROSAT
          </div>
          <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>
            Система агромониторинга на основе спутниковых данных Sentinel-2
          </div>
          <div style={{ fontSize: 12, color: '#666' }}>
            Для вопросов: <a href="mailto:support@agrosat.uz" style={{ color: '#22c55e' }}>support@agrosat.uz</a>
          </div>
          <div style={{ fontSize: 11, color: '#999', marginTop: 8 }}>
            Отчёт сгенерирован {reportDate}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────────────
function KpiBox({ label, value, color }) {
  return (
    <div style={{
      padding: '10px 14px',
      background: '#f9fafb',
      border: '1px solid #e5e7eb',
      borderRadius: 6,
    }}>
      <div style={{ fontSize: 11, color: '#888', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: color || '#1a1a1a' }}>{value}</div>
    </div>
  );
}

const printTh = {
  padding: '8px 10px',
  fontSize: 11,
  fontWeight: 700,
  textAlign: 'left',
  color: '#1a1a1a',
  borderBottom: '1px solid #d1d5db',
};

const printTd = {
  padding: '6px 10px',
  fontSize: 12,
  color: '#333',
  borderBottom: '1px solid #e5e7eb',
};
