import { PERIOD_PRESETS } from '../../utils/managementAnalytics.js';

const FOCUS = 'focus:outline-none focus:ring-2 focus:ring-agro-accent';

function OptionSelect({ id, label, value, onChange, options, allLabel, disabled, hint, testId }) {
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="block text-sm font-medium text-agro-text">{label}</label>
      <select
        id={id}
        data-testid={testId}
        className="input mt-1 min-h-11 w-full sm:text-sm disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-agro-muted"
        value={value}
        disabled={disabled}
        aria-describedby={hintId}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{allLabel}</option>
        {options.map((option) => <option key={option.id} value={String(option.id)}>{option.label}</option>)}
      </select>
      {hint && <p id={hintId} className="mt-1 text-xs leading-5 text-agro-muted">{hint}</p>}
    </div>
  );
}

/**
 * The single filter row above everything it scopes. Period presets and the
 * dimension selects apply at once; a custom period applies only through its
 * button and only when it is valid, so no partial or invalid date is sent.
 * Filters narrow the server-side scope; they never widen it.
 */
export default function AnalyticsFilters({
  isAdmin,
  filters,
  customPeriod,
  periodError,
  enterpriseOptions,
  managerEnterpriseLabel,
  fieldOptions,
  fieldOptionsState,
  cropOptions,
  cropOptionsState,
  busy,
  onPreset,
  onCustomPeriodChange,
  onCustomPeriodApply,
  onEnterpriseChange,
  onFieldChange,
  onCropChange,
  onReset,
  onReload,
}) {
  const fieldHint = isAdmin && !filters.enterpriseId
    ? 'Сначала выберите предприятие.'
    : fieldOptionsState === 'loading' ? 'Загружаем список полей…'
      : fieldOptionsState === 'error' ? 'Список полей не загружен. Остальные фильтры работают.'
        : null;
  const cropHint = filters.fieldId
    ? 'Для одного поля культура определяется самим полем.'
    : cropOptionsState === 'loading' ? 'Список появится после загрузки показателей.'
      : 'Культура, которая указана для поля сейчас.';

  return (
    <section aria-labelledby="management-analytics-filters-title" className="rounded-xl border border-agro-border bg-white p-3 sm:p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 id="management-analytics-filters-title" className="text-sm font-semibold text-agro-text">Фильтры</h2>
          <p className="mt-0.5 text-xs leading-5 text-agro-muted">
            Фильтры только сужают область, которую сервер определяет по вашей роли{isAdmin ? '' : ' и предприятию'}.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={onReload} disabled={busy} className={`btn-secondary min-h-11 px-3 text-sm disabled:opacity-60 ${FOCUS}`}>Обновить</button>
          <button type="button" onClick={onReset} className={`btn-secondary min-h-11 px-3 text-sm ${FOCUS}`}>Сбросить фильтры</button>
        </div>
      </div>

      <div className="mt-3 grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <fieldset className="min-w-0">
          <legend className="text-sm font-medium text-agro-text">Период</legend>
          <div className="mt-1 flex flex-wrap gap-2" role="group" aria-label="Быстрый выбор периода">
            {PERIOD_PRESETS.map((days) => {
              const active = filters.preset === days;
              return (
                <button
                  key={days}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onPreset(days)}
                  className={`min-h-11 rounded-lg border px-3 text-sm font-medium ${FOCUS} ${active ? 'border-agro-accent bg-emerald-50 text-agro-accent' : 'border-agro-border bg-white text-agro-text hover:bg-agro-hover'}`}
                >
                  {days} дней
                </button>
              );
            })}
          </div>
          <form className="mt-2 flex flex-wrap items-end gap-2" noValidate onSubmit={onCustomPeriodApply} aria-label="Свой период">
            <label className="text-sm text-agro-text">
              С даты
              <input
                type="date"
                data-testid="management-analytics-date-from"
                className="input mt-1 block min-h-11 w-40"
                value={customPeriod.dateFrom}
                aria-invalid={Boolean(periodError)}
                aria-describedby="management-analytics-period-help"
                onChange={(event) => onCustomPeriodChange({ dateFrom: event.target.value })}
              />
            </label>
            <label className="text-sm text-agro-text">
              По дату
              <input
                type="date"
                data-testid="management-analytics-date-to"
                className="input mt-1 block min-h-11 w-40"
                value={customPeriod.dateTo}
                aria-invalid={Boolean(periodError)}
                aria-describedby="management-analytics-period-help"
                onChange={(event) => onCustomPeriodChange({ dateTo: event.target.value })}
              />
            </label>
            <button type="submit" className={`btn-secondary min-h-11 px-3 text-sm ${FOCUS}`}>Применить период</button>
          </form>
          <p id="management-analytics-period-help" className={`mt-1 text-xs leading-5 ${periodError ? 'font-medium text-red-800' : 'text-agro-muted'}`} aria-live="polite">
            {periodError || 'Даты включительно, по календарю Ташкента; не больше 366 дней.'}
          </p>
        </fieldset>

        <div className="grid min-w-0 gap-3 sm:grid-cols-3">
          {isAdmin ? (
            <OptionSelect
              id="management-analytics-enterprise"
              testId="management-analytics-enterprise"
              label="Предприятие"
              value={filters.enterpriseId}
              onChange={onEnterpriseChange}
              options={enterpriseOptions}
              allLabel="Все предприятия"
            />
          ) : (
            <div className="min-w-0">
              <p className="text-sm font-medium text-agro-text">Предприятие</p>
              <p className="mt-1 flex min-h-11 items-center rounded-lg border border-agro-border bg-slate-50 px-3 text-sm text-agro-text" data-testid="management-analytics-own-enterprise">{managerEnterpriseLabel}</p>
              <p className="mt-1 text-xs leading-5 text-agro-muted">Закреплено сервером за вашей учётной записью.</p>
            </div>
          )}
          <OptionSelect
            id="management-analytics-field"
            testId="management-analytics-field"
            label="Поле"
            value={filters.fieldId}
            onChange={onFieldChange}
            options={fieldOptions}
            allLabel="Все поля"
            disabled={(isAdmin && !filters.enterpriseId) || fieldOptionsState === 'loading'}
            hint={fieldHint}
          />
          <OptionSelect
            id="management-analytics-crop"
            testId="management-analytics-crop"
            label="Текущая культура"
            value={filters.currentCropTypeId}
            onChange={onCropChange}
            options={cropOptions}
            allLabel="Все культуры"
            disabled={Boolean(filters.fieldId) || (cropOptionsState === 'loading' && !filters.currentCropTypeId)}
            hint={cropHint}
          />
        </div>
      </div>
    </section>
  );
}
