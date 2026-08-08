/**
 * EnterpriseDetailPage.jsx — Главная страница предприятия
 *
 * Роут: /enterprises/{id}
 *
 * Загружает только 3 запроса:
 *   1. Данные предприятия (GET /api/enterprises/{id}) — включает поля с current_ndvi
 *   2. Алерты предприятия (GET /api/alerts/?enterprise_id={id})
 *
 * NDVI история грузится только при клике «📊 График» — 1 запрос за раз.
 *
 * Состояния: loading, error (404 + общие), пустые данные
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import EnterpriseDashboard from '../components/Enterprise/EnterpriseDashboard';
import EnterpriseFieldsTable from '../components/Enterprise/EnterpriseFieldsTable';
import NDVIHistoryModal from '../components/Enterprise/NDVIHistoryModal';
import EnterpriseReport from '../components/Enterprise/EnterpriseReport';
import CommercialTenantPanel from '../components/Enterprise/CommercialTenantPanel';
import { getEnterprise, getAlerts } from '../api/client';
import client from '../api/client';
import { useAuth } from '../context/AuthContext';

// ─── Alert formatting helpers (TASK_019) ─────────────────────────────────
function formatAlertTitle(alert) {
  let title = alert.title || 'Алерт';

  // Strip out crazy percentages like (-185%) or (-500.3%) from the title
  // Replace them with absolute NDVI values when available
  const pctMatch = title.match(/\(([-+]?\d+(?:\.\d+)?)%\)/);
  if (pctMatch) {
    const pct = parseFloat(pctMatch[1]);
    // If percentage is sane (between -100 and +200), keep it
    // Otherwise strip it from the title
    if (pct < -100 || pct > 200) {
      title = title.replace(/\s*\([-+]?\d+(?:\.\d+)?%\)/, '');
    }
  }
  return title;
}

function formatAlertDescription(alert) {
  let desc = alert.description || '';

  // Detect ratios of the form "с X до Y" and ensure they show absolute NDVI
  // If the description contains an obviously broken percent, strip it
  desc = desc.replace(/\(\s*снижение на [-+]?\d{3,}(?:\.\d+)?%\s*\)/g, '');
  desc = desc.replace(/\(\s*[-+]?\d{3,}(?:\.\d+)?%\s*\)/g, '');

  // If we have triggered_value and threshold_value, append a clean absolute summary
  // ponytail: skip if triggered_value is a percentage (> 1.0) stored as NDVI
  if (alert.triggered_value !== null && alert.triggered_value !== undefined &&
      alert.threshold_value !== null && alert.threshold_value !== undefined &&
      Math.abs(Number(alert.triggered_value)) <= 1.0) {
    const cur = Number(alert.triggered_value).toFixed(3);
    const thr = Number(alert.threshold_value).toFixed(3);
    const delta = (Number(alert.triggered_value) - Number(alert.threshold_value)).toFixed(3);
    const deltaStr = delta >= 0 ? `+${delta}` : delta;
    if (!desc.includes('NDVI:')) {
      desc += desc ? ` ` : '';
      desc += `Текущий NDVI: ${cur}, порог: ${thr} (${deltaStr}).`;
    }
  }
  return desc.trim();
}
// ──────────────────────────────────────────────────────────────────────────

// ─── Lightweight markdown → React renderer for AI recommendations ──────────
function renderMarkdown(text) {
  if (!text) return null;

  const lines = text.split('\n');
  const elements = [];
  let listItems = [];
  let listType = null; // 'ul' or 'ol'

  const flushList = (key) => {
    if (listItems.length === 0) return;
    if (listType === 'ol') {
      elements.push(
        <ol key={`ol-${key}`} style={{ margin: '6px 0', paddingLeft: 20, color: '#1a2e23' }}>
          {listItems}
        </ol>
      );
    } else {
      elements.push(
        <ul key={`ul-${key}`} style={{ margin: '6px 0', paddingLeft: 20, color: '#1a2e23' }}>
          {listItems}
        </ul>
      );
    }
    listItems = [];
    listType = null;
  };

  // Inline bold parser: **text** → <strong>
  const parseInline = (str) => {
    const parts = str.split(/(\*\*[^*]+\*\*)/g);
    return parts.map((part, i) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        return <strong key={i} style={{ color: '#16a34a', fontWeight: 700 }}>{part.slice(2, -2)}</strong>;
      }
      // also handle *italic* (single asterisk)
      const italicParts = part.split(/(\*[^*]+\*)/g);
      return italicParts.map((ip, j) => {
        if (ip.startsWith('*') && ip.endsWith('*') && ip.length > 2) {
          return <em key={`${i}-${j}`} style={{ fontStyle: 'italic', color: '#22c55e' }}>{ip.slice(1, -1)}</em>;
        }
        return ip;
      });
    });
  };

  lines.forEach((line, idx) => {
    const trimmed = line.trim();

    // Headers: ### Title
    if (trimmed.startsWith('### ')) {
      flushList(idx);
      elements.push(
        <div key={idx} style={{
          fontSize: 14,
          fontWeight: 700,
          color: '#16a34a',
          marginTop: idx === 0 ? 0 : 14,
          marginBottom: 6,
        }}>
          {trimmed.slice(4)}
        </div>
      );
      return;
    }

    if (trimmed.startsWith('## ')) {
      flushList(idx);
      elements.push(
        <div key={idx} style={{
          fontSize: 15,
          fontWeight: 700,
          color: '#16a34a',
          marginTop: idx === 0 ? 0 : 14,
          marginBottom: 6,
        }}>
          {trimmed.slice(3)}
        </div>
      );
      return;
    }

    // Numbered list: 1. text
    const olMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
    if (olMatch) {
      if (listType !== 'ol') flushList(idx);
      listType = 'ol';
      listItems.push(
        <li key={`li-${idx}`} style={{ marginBottom: 4, lineHeight: 1.5 }}>
          {parseInline(olMatch[2])}
        </li>
      );
      return;
    }

    // Bullet list: - text or • text
    const ulMatch = trimmed.match(/^[-•]\s+(.*)/);
    if (ulMatch) {
      if (listType !== 'ul') flushList(idx);
      listType = 'ul';
      listItems.push(
        <li key={`li-${idx}`} style={{ marginBottom: 4, lineHeight: 1.5 }}>
          {parseInline(ulMatch[1])}
        </li>
      );
      return;
    }

    // Empty line
    if (trimmed === '' || trimmed === '---') {
      flushList(idx);
      return;
    }

    // Regular paragraph
    flushList(idx);
    elements.push(
      <p key={idx} style={{ margin: '4px 0', lineHeight: 1.5, color: '#1a2e23' }}>
        {parseInline(trimmed)}
      </p>
    );
  });

  flushList('final');
  return elements;
}

// ─── Вкладки ────────────────────────────────────────────────────────────────
const TABS = [
  { key: 'fields', label: 'Поля' },
  { key: 'recommendations', label: 'Рекомендации' },
];

// ─── Skeleton для страницы ──────────────────────────────────────────────────
function PageSkeleton() {
  return (
    <div style={{ padding: '0 24px 24px', height: '100%', overflowY: 'auto' }}>
      <div style={{ paddingTop: 24, marginBottom: 24, animation: 'pulse 1.5s ease-in-out infinite' }}>
        <div style={{ height: 28, width: '50%', background: '#e0e7e3', borderRadius: 6, marginBottom: 8 }} />
        <div style={{ height: 14, width: '30%', background: '#e0e7e3', borderRadius: 4 }} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 24 }}>
        {[1, 2, 3, 4, 5, 6].map(i => (
          <div key={i} style={{ height: 80, background: '#f8faf9', borderRadius: 10, border: '1px solid #e0e7e3' }} />
        ))}
      </div>
      {[1, 2, 3, 4, 5].map(i => (
        <div key={i} style={{ height: 40, background: i % 2 === 0 ? '#f1f5f3' : '#f8faf9', borderRadius: 4, marginBottom: 4 }} />
      ))}
    </div>
  );
}

// ─── 404 страница ──────────────────────────────────────────────────────────
function NotFound({ enterpriseId, onBack }) {
  return (
    <div style={{
      padding: '60px 24px',
      textAlign: 'center',
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
    }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>🌾</div>
      <h2 style={{ fontSize: 22, fontWeight: 700, color: '#1a2e23', margin: '0 0 8px' }}>
        Предприятие не найдено
      </h2>
      <p style={{ fontSize: 14, color: '#6b8578', marginBottom: 20 }}>
        Предприятие с ID #{enterpriseId} не существует или было удалено
      </p>
      <button onClick={onBack} className="btn-secondary" style={{ cursor: 'pointer' }}>
        ← Вернуться к предприятиям
      </button>
    </div>
  );
}

// ─── Error страница ─────────────────────────────────────────────────────────
function ErrorState({ message, onRetry }) {
  return (
    <div style={{
      padding: '60px 24px',
      textAlign: 'center',
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
    }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>⚠️</div>
      <h2 style={{ fontSize: 20, fontWeight: 700, color: '#ef4444', margin: '0 0 8px' }}>
        Ошибка загрузки
      </h2>
      <p style={{ fontSize: 14, color: '#6b8578', marginBottom: 20, maxWidth: 400 }}>
        {message || 'Не удалось загрузить данные предприятия'}
      </p>
      {onRetry && (
        <button onClick={onRetry} className="btn-primary" style={{ cursor: 'pointer' }}>
          Повторить
        </button>
      )}
    </div>
  );
}

// ─── Вкладка "Рекомендации" ────────────────────────────────────────────────
function RecommendationsTab({ alerts, loading, aiResults, aiLoading, onAIRecommend }) {
  const [visibleCount, setVisibleCount] = useState(10);
  const [tgSending, setTgSending] = useState({});  // keyed by alert.id
  const [tgSent, setTgSent] = useState({});        // keyed by alert.id

  async function handleSendTelegram(alertObj) {
    setTgSending(prev => ({ ...prev, [alertObj.id]: true }));
    try {
      await client.post('/api/telegram/send-alert', { alert_id: alertObj.id });
      setTgSent(prev => ({ ...prev, [alertObj.id]: true }));
      setTimeout(() => setTgSent(prev => ({ ...prev, [alertObj.id]: false })), 3000);
    } catch (err) {
      window.alert('Ошибка отправки в Telegram: ' + (err.response?.data?.detail || err.message));
    } finally {
      setTgSending(prev => ({ ...prev, [alertObj.id]: false }));
    }
  }

  if (loading) {
    return (
      <div style={{ padding: 20, textAlign: 'center', color: '#6b8578' }}>
        Загрузка рекомендаций...
      </div>
    );
  }

  const critical = (alerts || []).filter(a => a.severity === 'critical');
  const warning = (alerts || []).filter(a => a.severity === 'warning');

  const allItems = [
    ...critical.map(a => ({ ...a, _type: 'critical' })),
    ...warning.map(a => ({ ...a, _type: 'warning' })),
  ];

  const visibleItems = allItems.slice(0, visibleCount);

  if (allItems.length === 0) {
    return (
      <div style={{
        padding: '40px 20px',
        textAlign: 'center',
        color: '#16a34a',
        fontSize: 14,
      }}>
        ✅ Нет активных алертов. Все поля в норме.
      </div>
    );
  }

  return (
    <div>
      {visibleItems.map(alertItem => (
        <div
          key={alertItem.id}
          style={{
            padding: '12px 14px',
            background: alertItem._type === 'critical' ? '#fef2f2' : '#fffbeb',
            border: alertItem._type === 'critical' ? '1px solid #fecaca' : '1px solid #fde68a',
            borderRadius: 8,
            marginBottom: 8,
          }}
        >
          <div style={{
            fontSize: alertItem._type === 'critical' ? 13 : 12,
            fontWeight: 600,
            color: alertItem._type === 'critical' ? '#b91c1c' : '#a16207',
          }}>
            {alertItem._type === 'critical' ? '🔴 ' : '🟡 '}
            {formatAlertTitle(alertItem)}
          </div>
          {alertItem.description && (
            <div style={{
              fontSize: 12,
              color: alertItem._type === 'critical' ? '#b91c1c' : '#a16207',
              marginTop: 4,
              opacity: 0.8,
            }}>
              {formatAlertDescription(alertItem)}
            </div>
          )}
          {/* Snapshot date and cloud cover (B2) */}
          {(alertItem.captured_date || alertItem.cloud_cover_pct !== undefined) && (
            <div style={{
              fontSize: 10,
              color: '#94a3b8',
              marginTop: 6,
              display: 'flex',
              gap: 10,
              flexWrap: 'wrap',
            }}>
              {alertItem.captured_date && (
                <span>📅 Снимок: {new Date(alertItem.captured_date).toLocaleDateString('ru-RU')}</span>
              )}
              {alertItem.cloud_cover_pct !== undefined && alertItem.cloud_cover_pct !== null && (
                <span style={{ color: alertItem.cloud_cover_pct > 20 ? '#fbbf24' : '#94a3b8' }}>
                  ☁️ Облачность: {Math.round(alertItem.cloud_cover_pct)}%
                </span>
              )}
            </div>
          )}
          {alertItem.recommendation && (
            <div style={{
              fontSize: 12,
              color: '#16a34a',
              marginTop: 6,
              padding: '6px 8px',
              background: '#f1f5f3',
              borderRadius: 4,
              borderLeft: '3px solid #16a34a',
            }}>
              💡 {alertItem.recommendation}
            </div>
          )}
          {/* AI Button / Result */}
          <div style={{ marginTop: 10 }}>
            {!aiResults[alertItem.id] ? (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button
                  onClick={() => onAIRecommend(alertItem)}
                  disabled={aiLoading[alertItem.id]}
                  style={{
                    background: aiLoading[alertItem.id] ? '#e8eeea' : 'transparent',
                    border: '1px solid #16a34a',
                    borderRadius: 6,
                    padding: '6px 14px',
                    color: '#16a34a',
                    fontSize: 12,
                    cursor: aiLoading[alertItem.id] ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    transition: 'all 0.15s',
                  }}
                >
                  {aiLoading[alertItem.id] ? (
                    <>⏳ Анализирую...</>
                  ) : (
                    <>🤖 Углублённый AI анализ</>
                  )}
                </button>
                <button
                  onClick={() => handleSendTelegram(alertItem)}
                  disabled={tgSending[alertItem.id]}
                  style={{
                    background: tgSent[alertItem.id] ? '#15803d' : 'transparent',
                    border: '1px solid #e0e7e3',
                    borderRadius: 6,
                    padding: '6px 12px',
                    color: tgSent[alertItem.id] ? '#fff' : '#60a5fa',
                    fontSize: 12,
                    cursor: tgSending[alertItem.id] ? 'not-allowed' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  {tgSending[alertItem.id] ? '⏳ Отправка...' : tgSent[alertItem.id] ? '✓ Отправлено' : '✈️ В Telegram'}
                </button>
              </div>
            ) : (
              /* AI Result Card */
              <div style={{
                marginTop: 8,
                background: '#f0fdf4',
                border: '1px solid #bbf7d0',
                borderRadius: 8,
                padding: '12px 14px',
                fontSize: 13,
                color: '#1a2e23',
                lineHeight: 1.6,
              }}>
                <div style={{
                  fontSize: 11,
                  color: '#16a34a',
                  fontWeight: 600,
                  marginBottom: 8,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}>
                  🤖 AI Анализ (Claude)
                </div>
                {renderMarkdown(aiResults[alertItem.id])}
              </div>
            )}
          </div>
        </div>
      ))}

      {allItems.length > visibleCount && (
        <button
          onClick={() => setVisibleCount(v => v + 10)}
          style={{
            width: '100%',
            padding: '10px',
            background: '#f1f5f3',
            border: '1px solid #e0e7e3',
            borderRadius: 8,
            color: '#16a34a',
            cursor: 'pointer',
            fontSize: 13,
            marginTop: 8,
            transition: 'all 0.15s',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = '#e8eeea'; }}
          onMouseLeave={e => { e.currentTarget.style.background = '#f1f5f3'; }}
        >
          Показать ещё ({allItems.length - visibleCount} из {allItems.length})
        </button>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════════════

export default function EnterpriseDetailPage({ enterpriseId, onBack }) {
  const { user } = useAuth();
  const role = String(user?.role || '').toLowerCase();
  const [enterprise, setEnterprise] = useState(null);
  const [fields, setFields] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('fields');
  const [graphField, setGraphField] = useState(null);  // поле для NDVI модала
  const [aiResults, setAiResults] = useState({});   // keyed by alert.id
  const [aiLoading, setAiLoading] = useState({});    // keyed by alert.id

  // ─── Загрузка данных — только 2 запроса ──────────────────────────────────
  const fetchData = useCallback(async () => {
    if (!enterpriseId) return;
    setLoading(true);
    setError(null);
    try {
      const [entData, alertData] = await Promise.all([
        getEnterprise(enterpriseId),
        getAlerts({ enterprise_id: enterpriseId }).catch(() => []),
      ]);
      setEnterprise(entData);
      setFields(entData.fields || []);
      setAlerts(alertData?.length ? alertData : alertData?.items || []);
    } catch (err) {
      console.error('EnterpriseDetailPage error:', err);
      if (err.response?.status === 404) {
        setError('not_found');
      } else {
        setError('Не удалось загрузить данные предприятия');
      }
    } finally {
      setLoading(false);
    }
  }, [enterpriseId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // ─── Клик «📊 График» — просто открываем модал (NDVI грузит сам модал) ────
  const handleViewGraph = useCallback((field) => {
    setGraphField(field);
  }, []);

  const handleCloseGraph = useCallback(() => {
    setGraphField(null);
  }, []);

  // ─── Скачать PDF отчёт (серверная генерация) ──────────────────────────────
  const handleDownloadPDF = useCallback(() => {
    window.open(
      `/api/reports/enterprise/${enterpriseId}/pdf`,
      '_blank'
    );
  }, [enterpriseId]);

  // ─── AI Анализ ────────────────────────────────────────────────────────────
  const handleAIRecommend = useCallback(async (alertObj) => {
    if (aiResults[alertObj.id]) return;
    setAiLoading(prev => ({ ...prev, [alertObj.id]: true }));
    try {
      const res = await client.post('/api/ai/recommend', {
        alert_id: alertObj.id,
        field_id: alertObj.field_id,
      });
      setAiResults(prev => ({ ...prev, [alertObj.id]: res.data.recommendation }));
    } catch (err) {
      const detail = err.response?.data?.detail;
      setAiResults(prev => ({ ...prev, [alertObj.id]: detail
        ? `⚠️ ${detail}`
        : '⚠️ Ошибка получения рекомендации. Попробуйте позже.'
      }));
    } finally {
      setAiLoading(prev => ({ ...prev, [alertObj.id]: false }));
    }
  }, [aiResults]);

  // ─── Последнее обновление ─────────────────────────────────────────────────
  const lastUpdated = useMemo(() => {
    if (!fields || fields.length === 0) return null;
    const dates = fields.map(f => f.last_ndvi_date).filter(Boolean).sort().reverse();
    return dates.length > 0 ? dates[0] : null;
  }, [fields]);
  const tabs = useMemo(
    () => role === 'admin' || role === 'manager'
      ? [...TABS, { key: 'commercial', label: 'Коммерческий контур' }]
      : TABS,
    [role],
  );

  if (error === 'not_found') {
    return <NotFound enterpriseId={enterpriseId} onBack={onBack} />;
  }

  if (error && error !== 'not_found') {
    return <ErrorState message={error} onRetry={fetchData} />;
  }

  if (loading) {
    return <PageSkeleton />;
  }

  // ─── Render ───────────────────────────────────────────────────────────────
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Шапка */}
      <div className="flex-col px-6 pb-4 pt-20 md:flex-row md:pt-4" style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: 12,
        borderBottom: '1px solid #e0e7e3',
        flexShrink: 0,
      }}>
        <div>
          {/* Кнопка назад */}
          {onBack && (
            <button onClick={onBack} style={{
              background: 'none',
              border: 'none',
              color: '#16a34a',
              cursor: 'pointer',
              fontSize: 12,
              padding: 0,
              marginBottom: 6,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}>
              ← Предприятия
            </button>
          )}

          {/* Название */}
          <h2 style={{
            fontSize: 20,
            fontWeight: 700,
            color: '#1a2e23',
            margin: 0,
            lineHeight: 1.2,
          }}>
            {enterprise?.name}
          </h2>

          {/* Мета: код, регион, дата */}
          <div style={{
            fontSize: 12,
            color: '#6b8578',
            marginTop: 4,
            display: 'flex',
            gap: 8,
            flexWrap: 'wrap',
          }}>
            {enterprise?.code && <span>Код: {enterprise.code}</span>}
            {enterprise?.code && enterprise?.region && <span>•</span>}
            {enterprise?.region && <span>{enterprise.region}</span>}
            {lastUpdated && <><span>•</span><span>Обновлено: {new Date(lastUpdated).toLocaleDateString('ru-RU')}</span></>}
          </div>
        </div>

        {/* Кнопки */}
        <div className="w-full flex-wrap md:w-auto" style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          <button onClick={handleDownloadPDF} style={{
            background: '#f1f5f3',
            border: '1px solid #e0e7e3',
            borderRadius: 8,
            padding: '8px 14px',
            color: '#16a34a',
            cursor: 'pointer',
            fontSize: 12,
            whiteSpace: 'nowrap',
            transition: 'all 0.15s',
          }}
            onMouseEnter={e => { e.currentTarget.style.background = '#e8eeea'; }}
            onMouseLeave={e => { e.currentTarget.style.background = '#f1f5f3'; }}
          >
            📄 Скачать отчёт PDF
          </button>
          <button
            onClick={async () => {
              try {
                await client.post('/api/telegram/test');
                window.alert('✅ Тестовое сообщение отправлено в Telegram');
              } catch (err) {
                window.alert('❌ ' + (err.response?.data?.detail || 'Telegram не настроен'));
              }
            }}
            style={{
              background: '#f1f5f3',
              border: '1px solid #e0e7e3',
              borderRadius: 8,
              padding: '8px 14px',
              color: '#3b82f6',
              cursor: 'pointer',
              fontSize: 12,
              whiteSpace: 'nowrap',
            }}
          >
            ✈️ Тест Telegram
          </button>
        </div>
      </div>

      {/* Scrollable content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 24px 24px' }}>
        {/* KPI Dashboard */}
        <EnterpriseDashboard
          enterprise={enterprise}
          fields={fields}
          alerts={alerts}
        />

        {/* Tab Bar */}
        <div style={{
          display: 'flex',
          gap: 0,
          borderBottom: '1px solid #e0e7e3',
          marginBottom: 16,
        }}>
          {tabs.map(tab => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              style={{
                background: 'none',
                border: 'none',
                borderBottom: activeTab === tab.key ? '2px solid #16a34a' : '2px solid transparent',
                color: activeTab === tab.key ? '#16a34a' : '#6b8578',
                fontWeight: activeTab === tab.key ? 600 : 400,
                padding: '8px 16px',
                cursor: 'pointer',
                fontSize: 14,
                transition: 'all 0.15s',
                marginBottom: -1,
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {activeTab === 'fields' && (
          <EnterpriseFieldsTable
            fields={fields}
            alerts={alerts}
            onViewGraph={handleViewGraph}
            loading={false}
          />
        )}

        {activeTab === 'recommendations' && (
          <RecommendationsTab
            alerts={alerts}
            loading={false}
            aiResults={aiResults}
            aiLoading={aiLoading}
            onAIRecommend={handleAIRecommend}
          />
        )}

        {activeTab === 'commercial' && (
          <CommercialTenantPanel enterpriseId={enterpriseId} role={role} />
        )}
      </div>

      {/* NDVI History Modal — грузит данные сам при открытии */}
      {graphField && (
        <NDVIHistoryModal
          field={graphField}
          onClose={handleCloseGraph}
        />
      )}
    </div>
  );
}
