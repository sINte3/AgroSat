import { useState, useEffect } from 'react';

const IRRIGATION_OPTIONS = [
  { value: '', label: '— не указано —' },
  { value: 'canal', label: 'Канальный' },
  { value: 'drip', label: 'Капельный' },
  { value: 'sprinkler', label: 'Дождевальный' },
  { value: 'rainfed', label: 'Богарный' },
];

const CODE_REGEX = /^[A-Z0-9][A-Z0-9_-]{0,49}$/;
const NAME_FORBIDDEN = /[\x00-\x1F\x7F<>{}\[\]`\\|]/;

function fieldError(msg) {
  return <p className="text-xs text-red-500 mt-1">{msg}</p>;
}

export default function CreateFieldModal({
  geometry,
  enterprises,
  currentUser,
  defaultEnterpriseId,
  saving,
  error,
  onCancel,
  onSubmit,
}) {
  const isAgronomist = currentUser?.role === 'agronomist';

  function resolveDefaultEnterprise() {
    if (isAgronomist && currentUser?.enterprise_id) {
      return String(currentUser.enterprise_id);
    }
    if (defaultEnterpriseId) return String(defaultEnterpriseId);
    if (enterprises?.length) return String(enterprises[0].id);
    return '';
  }

  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [enterpriseId, setEnterpriseId] = useState(resolveDefaultEnterprise);
  const [irrigationType, setIrrigationType] = useState('');
  const [soilType, setSoilType] = useState('');
  const [notes, setNotes] = useState('');
  const [validationErrors, setValidationErrors] = useState({});

  // Re-sync enterprise when enterprises list loads asynchronously
  useEffect(() => {
    if (!enterpriseId && enterprises?.length) {
      setEnterpriseId(resolveDefaultEnterprise());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enterprises]);

  function validate() {
    const errs = {};
    const trimmedName = name.trim();

    if (trimmedName.length < 2 || trimmedName.length > 255) {
      errs.name = 'Название должно содержать от 2 до 255 символов.';
    } else if (NAME_FORBIDDEN.test(trimmedName)) {
      errs.name = 'Название содержит недопустимые символы: < > { } [ ] ` \\ |';
    }

    if (code.trim()) {
      const upperCode = code.trim().toUpperCase();
      if (upperCode.length > 50) {
        errs.code = 'Код не должен превышать 50 символов.';
      } else if (!CODE_REGEX.test(upperCode)) {
        errs.code = 'Код должен начинаться с буквы или цифры и содержать только A–Z, 0–9, _ и -.';
      }
    }

    if (!enterpriseId) {
      errs.enterpriseId = 'Выберите предприятие.';
    }

    if (!geometry || geometry.type !== 'Polygon') {
      errs.geometry = 'Геометрия поля не определена или имеет неверный тип.';
    }

    return errs;
  }

  function handleSubmit(e) {
    e.preventDefault();
    const errs = validate();
    setValidationErrors(errs);
    if (Object.keys(errs).length > 0) return;

    const trimmedName = name.trim();
    const trimmedCode = code.trim().toUpperCase() || null;
    const trimmedSoilType = soilType.trim() || null;
    const trimmedNotes = notes.trim() || null;

    onSubmit({
      enterprise_id: Number(enterpriseId),
      name: trimmedName,
      code: trimmedCode,
      geometry,
      irrigation_type: irrigationType || null,
      soil_type: trimmedSoilType,
      notes: trimmedNotes,
    });
  }

  const isViewer = currentUser?.role === 'viewer';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm px-4">
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[92vh] flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-field-modal-title"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
          <h2 id="create-field-modal-title" className="text-lg font-bold text-slate-800">
            Добавить поле
          </h2>
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="text-slate-400 hover:text-slate-600 transition-colors disabled:opacity-40"
            aria-label="Закрыть"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 px-6 py-4 space-y-4">
          {/* Geometry hint */}
          <div className="rounded-lg bg-green-50 border border-green-200 px-3 py-2 text-xs text-green-700">
            🗺 Геометрия получена с карты. Площадь будет рассчитана сервером.
          </div>

          {/* API error */}
          {error && (
            <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">
              {error}
            </div>
          )}

          {/* Viewer guard */}
          {isViewer && (
            <div className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-700">
              У вас нет прав для создания полей.
            </div>
          )}

          {/* Geometry validation error */}
          {validationErrors.geometry && fieldError(validationErrors.geometry)}

          {/* Name */}
          <div>
            <label htmlFor="cf-name" className="block text-xs font-semibold text-slate-600 mb-1">
              Название поля <span className="text-red-500">*</span>
            </label>
            <input
              id="cf-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={saving || isViewer}
              placeholder="Например: Поле Северное"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-green-400 disabled:bg-slate-50 disabled:text-slate-400"
            />
            {validationErrors.name && fieldError(validationErrors.name)}
          </div>

          {/* Code */}
          <div>
            <label htmlFor="cf-code" className="block text-xs font-semibold text-slate-600 mb-1">
              Код поля <span className="text-slate-400 font-normal">(необязательно)</span>
            </label>
            <input
              id="cf-code"
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              disabled={saving || isViewer}
              placeholder="Например: FIELD-01"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-green-400 disabled:bg-slate-50 disabled:text-slate-400"
            />
            <p className="text-xs text-slate-400 mt-1">
              Только A–Z, 0–9, _ и -. Будет приведён к верхнему регистру.
            </p>
            {validationErrors.code && fieldError(validationErrors.code)}
          </div>

          {/* Enterprise */}
          <div>
            <label htmlFor="cf-enterprise" className="block text-xs font-semibold text-slate-600 mb-1">
              Предприятие <span className="text-red-500">*</span>
            </label>
            <select
              id="cf-enterprise"
              value={enterpriseId}
              onChange={(e) => setEnterpriseId(e.target.value)}
              disabled={saving || isViewer || isAgronomist}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-green-400 disabled:bg-slate-50 disabled:text-slate-400"
            >
              {enterprises?.map((ent) => (
                <option key={ent.id} value={String(ent.id)}>
                  {ent.name}
                </option>
              ))}
            </select>
            {isAgronomist && (
              <p className="text-xs text-slate-400 mt-1">Предприятие зафиксировано для вашей роли.</p>
            )}
            {validationErrors.enterpriseId && fieldError(validationErrors.enterpriseId)}
          </div>

          {/* Irrigation type */}
          <div>
            <label htmlFor="cf-irrigation" className="block text-xs font-semibold text-slate-600 mb-1">
              Тип орошения <span className="text-slate-400 font-normal">(необязательно)</span>
            </label>
            <select
              id="cf-irrigation"
              value={irrigationType}
              onChange={(e) => setIrrigationType(e.target.value)}
              disabled={saving || isViewer}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-green-400 disabled:bg-slate-50 disabled:text-slate-400"
            >
              {IRRIGATION_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {/* Soil type */}
          <div>
            <label htmlFor="cf-soil" className="block text-xs font-semibold text-slate-600 mb-1">
              Тип почвы <span className="text-slate-400 font-normal">(необязательно)</span>
            </label>
            <input
              id="cf-soil"
              type="text"
              value={soilType}
              onChange={(e) => setSoilType(e.target.value)}
              disabled={saving || isViewer}
              placeholder="Например: Серозём"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-green-400 disabled:bg-slate-50 disabled:text-slate-400"
            />
          </div>

          {/* Notes */}
          <div>
            <label htmlFor="cf-notes" className="block text-xs font-semibold text-slate-600 mb-1">
              Заметки <span className="text-slate-400 font-normal">(необязательно)</span>
            </label>
            <textarea
              id="cf-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={saving || isViewer}
              rows={3}
              placeholder="Дополнительная информация о поле..."
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-green-400 resize-none disabled:bg-slate-50 disabled:text-slate-400"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-100">
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-medium text-slate-600 bg-slate-100 hover:bg-slate-200 transition-colors disabled:opacity-40"
          >
            Отмена
          </button>
          {!isViewer && (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={saving}
              className="px-5 py-2 rounded-lg text-sm font-semibold text-white bg-green-600 hover:bg-green-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {saving && (
                <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                </svg>
              )}
              {saving ? 'Сохранение...' : 'Сохранить поле'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

