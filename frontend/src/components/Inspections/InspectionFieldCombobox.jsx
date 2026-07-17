import { useEffect, useId, useMemo, useRef, useState } from 'react';

export const FIELD_RESULT_LIMIT = 12;

function fieldName(field) {
  return typeof field?.name === 'string' ? field.name.trim() : '';
}

export default function InspectionFieldCombobox({
  fields,
  value,
  onChange,
  loading,
  disabled,
  inputRef,
}) {
  const componentId = useId();
  const inputId = `${componentId}-input`;
  const listboxId = `${componentId}-listbox`;
  const rootRef = useRef(null);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const selectedField = useMemo(
    () => fields.find((field) => String(field?.id) === String(value)),
    [fields, value],
  );
  const normalizedQuery = query.trim().toLocaleLowerCase('ru-RU');
  const matches = useMemo(() => fields.filter((field) => {
    const name = fieldName(field);
    if (!name || !Number.isSafeInteger(Number(field?.id)) || Number(field.id) <= 0) return false;
    if (!normalizedQuery) return true;
    return name.toLocaleLowerCase('ru-RU').includes(normalizedQuery)
      || String(field.id).includes(normalizedQuery);
  }), [fields, normalizedQuery]);
  const visibleFields = matches.slice(0, FIELD_RESULT_LIMIT);
  const visibleResultKey = visibleFields.map((field) => field.id).join(',');

  useEffect(() => {
    if (value && selectedField) setQuery(fieldName(selectedField));
  }, [selectedField, value]);

  useEffect(() => {
    setActiveIndex(-1);
  }, [normalizedQuery, visibleResultKey]);

  function selectField(field) {
    onChange(String(field.id));
    setQuery(fieldName(field));
    setOpen(false);
    setActiveIndex(-1);
    inputRef.current?.focus();
  }

  function handleInputChange(event) {
    setQuery(event.target.value);
    if (value) onChange('');
    setOpen(true);
  }

  function handleKeyDown(event) {
    if (event.key === 'Escape' && open) {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (!visibleFields.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => (current + 1) % visibleFields.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => (current <= 0 ? visibleFields.length - 1 : current - 1));
    } else if (event.key === 'Home') {
      event.preventDefault();
      setOpen(true);
      setActiveIndex(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      setOpen(true);
      setActiveIndex(visibleFields.length - 1);
    } else if (event.key === 'Enter' && open && activeIndex >= 0) {
      event.preventDefault();
      selectField(visibleFields[activeIndex]);
    }
  }

  function handleBlur(event) {
    if (!rootRef.current?.contains(event.relatedTarget)) {
      setOpen(false);
      setActiveIndex(-1);
    }
  }

  function clearSelection() {
    onChange('');
    setQuery('');
    setOpen(true);
    setActiveIndex(-1);
    inputRef.current?.focus();
  }

  let status = '';
  if (loading) status = 'Загружаем поля…';
  else if (!fields.length) status = 'Доступные поля не найдены';
  else if (!matches.length) status = 'Поля не найдены';
  else if (matches.length > FIELD_RESULT_LIMIT) {
    status = `Показано ${FIELD_RESULT_LIMIT} из ${matches.length}. Уточните поиск.`;
  }

  return (
    <div ref={rootRef} className="relative text-sm" onBlur={handleBlur}>
      <label htmlFor={inputId} className="block">Поле</label>
      <span className="sr-only" id={`${componentId}-instructions`}>Выберите поле из списка результатов. Для отправки формы требуется явный выбор.</span>
      <div className="relative mt-1 flex">
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-activedescendant={open && activeIndex >= 0 ? `${componentId}-option-${visibleFields[activeIndex]?.id}` : undefined}
          aria-describedby={`${componentId}-instructions`}
          aria-required="true"
          autoComplete="off"
          disabled={disabled || loading}
          placeholder="Введите номер или название поля"
          className="input min-h-11 w-full py-2 pl-3 pr-12 focus:outline-none focus:ring-2 focus:ring-agro-accent"
          value={query}
          onChange={handleInputChange}
          onClick={() => setOpen(true)}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
        />
        {value && (
          <button
            type="button"
            aria-label="Очистить выбранное поле"
            disabled={disabled}
            onClick={clearSelection}
            className="absolute right-0 top-0 inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-agro-muted hover:bg-agro-hover focus:outline-none focus:ring-2 focus:ring-agro-accent"
          >
            <span aria-hidden="true">×</span>
          </button>
        )}
      </div>
      {open && (
        <div className="mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-agro-border bg-white p-1 shadow-lg">
          <div id={listboxId} role="listbox" aria-label="Результаты поиска полей">
            {visibleFields.map((field, index) => {
              const selected = String(field.id) === String(value);
              const active = index === activeIndex;
              return (
                <button
                  key={field.id}
                  id={`${componentId}-option-${field.id}`}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  tabIndex={-1}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => selectField(field)}
                  className={`mb-1 min-h-11 w-full rounded-lg border px-3 py-2 text-left last:mb-0 focus:outline-none ${selected ? 'border-emerald-200 bg-emerald-50 text-agro-text' : 'border-transparent'} ${active ? 'bg-agro-hover ring-2 ring-agro-accent' : 'hover:bg-agro-hover'}`}
                >
                  <span className="block break-words font-medium">{fieldName(field)}</span>
                  <span className="block text-xs text-agro-muted">Поле #{field.id}{selected ? ' · Выбрано' : ''}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      {status && <p className="mt-1 text-sm text-agro-muted" aria-live="polite">{status}</p>}
    </div>
  );
}
