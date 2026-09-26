import { useState } from 'react';

import {
  BREAKDOWN_CURRENT_COLUMNS,
  BREAKDOWN_PERIOD_COLUMNS,
  NO_CROP_LABEL,
  currentCropNote,
} from '../../config/managementAnalytics.js';
import { formatCount, formatPeriod, positiveId } from '../../utils/managementAnalytics.js';

const FOCUS = 'focus:outline-none focus:ring-2 focus:ring-agro-accent';

const DIMENSIONS = Object.freeze([
  { id: 'enterprises', label: 'Предприятия', heading: 'Предприятие' },
  { id: 'current_crops', label: 'Текущие культуры', heading: 'Текущая культура' },
  { id: 'fields', label: 'Поля', heading: 'Поле' },
]);

function Toggle({ label, options, value, onChange }) {
  return (
    <div role="group" aria-label={label} className="flex max-w-full flex-wrap overflow-hidden rounded-lg border border-agro-border">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          aria-pressed={value === option.id}
          onClick={() => onChange(option.id)}
          className={`min-h-11 px-3 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-inset focus:ring-agro-accent ${value === option.id ? 'bg-emerald-50 text-agro-accent' : 'bg-white text-agro-text hover:bg-agro-hover'}`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function RowName({ dimension, row, canDrill, onDrill }) {
  let name;
  let detail = null;
  let drillLabel;
  if (dimension === 'enterprises') {
    name = row.enterprise_name || `Предприятие #${row.enterprise_id}`;
    drillLabel = `Показать аналитику предприятия «${name}»`;
  } else if (dimension === 'current_crops') {
    name = row.current_crop_name || NO_CROP_LABEL;
    drillLabel = `Показать аналитику по текущей культуре «${name}»`;
  } else {
    name = row.field_name || `Поле #${row.field_id}`;
    detail = `${row.enterprise_name || `Предприятие #${row.enterprise_id}`} · ${row.current_crop_name || NO_CROP_LABEL}`;
    drillLabel = `Показать аналитику поля «${name}»`;
  }
  return (
    <>
      {canDrill ? (
        <button type="button" onClick={onDrill} aria-label={drillLabel} className={`min-h-11 text-left font-medium text-agro-accent underline decoration-agro-accent/40 underline-offset-2 hover:decoration-agro-accent ${FOCUS}`}>{name}</button>
      ) : (
        <span className="font-medium text-agro-text">{name}</span>
      )}
      {detail && <span className="block text-xs font-normal text-agro-muted">{detail}</span>}
    </>
  );
}

/**
 * Enterprise, current-crop and field rows of the same snapshot. Rows keep the
 * server order: the field list is a server page (active problems, then overdue
 * cases, then resolved cycles), so sorting one page in the browser would
 * misrepresent the whole list.
 */
export default function BreakdownSection({
  data,
  isAdmin,
  onDrillEnterprise,
  onDrillCrop,
  onDrillField,
  onFieldPage,
  paging,
}) {
  const scope = data.scope || {};
  const breakdowns = data.breakdowns || {};
  const effective = data.period?.effective || {};
  const enterprisesAvailable = scope.enterprise_id === null || scope.enterprise_id === undefined;
  const dimensions = DIMENSIONS.filter((item) => item.id !== 'enterprises' || enterprisesAvailable);
  const [chosenDimension, setChosenDimension] = useState(enterprisesAvailable ? 'enterprises' : 'fields');
  const [columnGroup, setColumnGroup] = useState('current');
  const dimension = dimensions.some((item) => item.id === chosenDimension) ? chosenDimension : 'fields';
  const heading = DIMENSIONS.find((item) => item.id === dimension).heading;
  const columns = columnGroup === 'current' ? BREAKDOWN_CURRENT_COLUMNS : BREAKDOWN_PERIOD_COLUMNS;
  const fieldPage = breakdowns.fields || { items: [], total: 0, limit: 0, offset: 0 };
  const rows = dimension === 'fields' ? (fieldPage.items || []) : (breakdowns[dimension] || []);
  const rowKey = (row) => (dimension === 'enterprises' ? `e${row.enterprise_id}` : dimension === 'current_crops' ? `c${row.current_crop_type_id ?? 'none'}` : `f${row.field_id}`);
  const canDrill = (row) => {
    if (dimension === 'enterprises') return isAdmin && Boolean(positiveId(row.enterprise_id));
    if (dimension === 'current_crops') return !scope.field_id && !scope.current_crop_type_id && Boolean(positiveId(row.current_crop_type_id));
    return !scope.field_id && Boolean(positiveId(row.field_id));
  };
  const drill = (row) => {
    if (dimension === 'enterprises') onDrillEnterprise(row.enterprise_id);
    else if (dimension === 'current_crops') onDrillCrop(row.current_crop_type_id);
    else onDrillField(row);
  };
  const first = fieldPage.total ? fieldPage.offset + 1 : 0;
  const last = Math.min(fieldPage.offset + (fieldPage.items || []).length, fieldPage.total);

  return (
    <section aria-labelledby="management-analytics-breakdown-title" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="management-analytics-breakdown-title" className="text-lg font-semibold text-agro-text">Разбивка</h2>
        <p className="text-xs text-agro-muted">Кто и что формирует нагрузку и результаты</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Toggle label="Разрез" options={dimensions} value={dimension} onChange={setChosenDimension} />
        <Toggle
          label="Группа показателей"
          options={[{ id: 'current', label: 'Нагрузка сейчас' }, { id: 'period', label: 'Итоги периода' }]}
          value={columnGroup}
          onChange={setColumnGroup}
        />
      </div>
      <p className="text-xs leading-5 text-agro-muted">
        {columnGroup === 'current'
          ? 'Состояние на момент формирования; период на эти числа не влияет. Работы с истёкшим сроком считаются поштучно, а не по проблемам.'
          : `События за период ${formatPeriod(effective.date_from, effective.date_to)}. Результат — только завершённая спутниковая проверка.`}
        {dimension === 'current_crops' && ` ${currentCropNote(data.crop_classification?.reference_year)}`}
        {dimension === 'fields' && ' Порядок задаёт сервер: больше активных проблем, затем просроченных ситуаций, затем решённых циклов.'}
      </p>
      <div className={`overflow-x-auto rounded-xl border border-agro-border bg-white ${FOCUS}`} role="region" tabIndex={0} aria-label="Таблица разбивки, прокручивается по горизонтали">
        <table className="w-full min-w-[900px] text-sm" data-testid="management-analytics-breakdown" data-dimension={dimension} data-columns={columnGroup}>
          <caption className="sr-only">Разбивка: {DIMENSIONS.find((item) => item.id === dimension).label}, {columnGroup === 'current' ? 'нагрузка сейчас' : 'итоги периода'}</caption>
          <thead>
            <tr className="border-b border-agro-border bg-slate-50 text-xs text-agro-muted">
              <th scope="col" className="sticky left-0 z-[1] min-w-[11rem] bg-slate-50 px-3 py-2 text-left font-medium shadow-[1px_0_0_0_#e0e7e3] sm:min-w-[15rem]">{heading}</th>
              {columns.map((column) => <th key={column.id} scope="col" className="px-3 py-2 text-right align-bottom font-medium">{column.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={columns.length + 1} className="px-3 py-6 text-center text-sm text-agro-muted">В выбранной области нет строк для этого разреза.</td></tr>
            )}
            {rows.map((row) => (
              <tr key={rowKey(row)} className="border-b border-agro-border/60 last:border-b-0">
                <th scope="row" className="sticky left-0 z-[1] min-w-[11rem] bg-white px-3 py-1 text-left align-middle font-normal shadow-[1px_0_0_0_#e0e7e3] sm:min-w-[15rem]">
                  <RowName dimension={dimension} row={row} canDrill={canDrill(row)} onDrill={() => drill(row)} />
                </th>
                {columns.map((column) => <td key={column.id} className="px-3 py-2 text-right tabular-nums text-agro-text">{formatCount(column.read(row))}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {dimension === 'fields' && (
        <nav className="flex flex-wrap items-center justify-between gap-3" aria-label="Страницы списка полей">
          <button type="button" className={`btn-secondary min-h-11 px-4 text-sm disabled:opacity-50 ${FOCUS}`} disabled={paging || fieldPage.offset === 0} onClick={() => onFieldPage(Math.max(0, fieldPage.offset - fieldPage.limit))}>Назад</button>
          <span className="text-sm text-agro-muted" data-testid="management-analytics-field-page">{fieldPage.total ? `${formatCount(first)}–${formatCount(last)} из ${formatCount(fieldPage.total)}` : 'Полей нет'}</span>
          <button type="button" className={`btn-secondary min-h-11 px-4 text-sm disabled:opacity-50 ${FOCUS}`} disabled={paging || last >= fieldPage.total} onClick={() => onFieldPage(fieldPage.offset + fieldPage.limit)}>Далее</button>
        </nav>
      )}
    </section>
  );
}
