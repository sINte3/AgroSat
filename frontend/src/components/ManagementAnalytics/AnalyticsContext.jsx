import { MANAGEMENT_ANALYTICS_DEFINITIONS_VERSION } from '../../config/managementAnalytics.js';
import { formatCount, formatPeriod } from '../../utils/managementAnalytics.js';
import { formatTashkentDateTime } from '../../utils/tashkentTime.js';

const list = (value) => (Array.isArray(value) ? value.filter((item) => typeof item === 'string') : []);

/**
 * What the numbers below describe: the effective period and scope returned by
 * the server, when they were generated, and how they are defined. The
 * definitions version stays available here without being the headline.
 */
export default function AnalyticsContext({ data, scopeLabel }) {
  const effective = data?.period?.effective || {};
  const provenance = data?.provenance || {};
  const fingerprint = typeof provenance.definitions_fingerprint === 'string' ? provenance.definitions_fingerprint : '';
  const version = typeof data?.definitions_version === 'string' ? data.definitions_version : 'не указана';
  return (
    <section aria-label="Контекст показателей" className="rounded-xl border border-agro-border bg-white px-4 py-3" data-testid="management-analytics-context">
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 xl:flex xl:flex-wrap">
        <div className="min-w-0">
          <dt className="text-xs text-agro-muted">Период итогов</dt>
          <dd className="font-medium text-agro-text" data-testid="management-analytics-period">
            {formatPeriod(effective.date_from, effective.date_to)} · {formatCount(effective.days)} дн. включительно
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-agro-muted">Область</dt>
          <dd className="break-words font-medium text-agro-text" data-testid="management-analytics-scope">{scopeLabel}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-agro-muted">Данные сформированы</dt>
          <dd className="font-medium text-agro-text" data-testid="management-analytics-generated">{formatTashkentDateTime(data?.generated_at, 'время не указано')} · Ташкент</dd>
        </div>
      </dl>
      <details className="mt-2 border-t border-agro-border pt-2" data-testid="management-analytics-definitions">
        <summary className="flex min-h-11 cursor-pointer items-center text-sm font-medium text-agro-accent focus:outline-none focus:ring-2 focus:ring-agro-accent">
          Как считаются показатели · единые определения (версия {version === MANAGEMENT_ANALYTICS_DEFINITIONS_VERSION ? 'v1' : version})
        </summary>
        <div className="space-y-3 pb-2 pt-1 text-sm leading-6 text-agro-text">
          <p>
            Версия определений: <code className="rounded bg-slate-100 px-1 text-xs">{version}</code>
            {' · '}правило спутниковой проверки: <code className="rounded bg-slate-100 px-1 text-xs">{provenance.verification_policy_version || 'не указано'}</code>
            {' · '}часовой пояс: {data?.timezone || 'не указан'}
          </p>
          <ul className="list-disc space-y-1 pl-5 text-agro-muted">
            <li><strong className="text-agro-text">Сейчас</strong> — состояние на момент формирования; период его не сужает. <strong className="text-agro-text">За период</strong> — события, время которых попало в выбранные даты.</li>
            <li>Проблемы и их состояния — те же ситуации и статусы, что в Операционном центре. «Просроченные ситуации» совпадают с его показателем «Просрочено». «Работы с истёкшим сроком» — отдельные работы, срок которых уже прошёл; это не показатель «Просроченные работы по планам» из «Отчётов», который считается по дате конца периода.</li>
            <li>Результат меры — только завершённая спутниковая проверка: улучшение, без существенных изменений или ухудшение. Нет снимка, рано, облачность, качество, источник и «недостаточно доказательств» результатом не считаются.</li>
            <li>Завершённые работы — не успех. Успех определяется только результатом проверки.</li>
            <li>Культура — текущая культура поля, а не культура на момент события.</li>
            <li>История прежнего контура корректирующих действий (TASK_209) не используется.</li>
          </ul>
          {list(provenance.sources).length > 0 && (
            <p className="text-xs text-agro-muted">Источники: {list(provenance.sources).join(', ')}. Не используются: {list(provenance.excluded_legacy_sources).join(', ') || '—'}.</p>
          )}
          {fingerprint && <p className="text-xs text-agro-muted">Отпечаток определений: <code>{fingerprint.slice(0, 16)}</code></p>}
          {list(data?.limitations).length > 0 && (
            <div>
              <p className="text-xs font-medium text-agro-muted">Ограничения, опубликованные сервером (на английском):</p>
              <ul lang="en" className="mt-1 list-disc space-y-1 pl-5 text-xs text-agro-muted">
                {list(data.limitations).map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>
          )}
        </div>
      </details>
    </section>
  );
}
