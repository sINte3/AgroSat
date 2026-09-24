import { parseTashkentDateTimeInput, toTashkentDateTimeInput } from './tashkentTime.js';

/**
 * Validates the raw datetime-local text of an agronomy work item on submit.
 * Nothing is converted while the user types: partial input stays raw text and
 * can never throw during rendering.
 *
 * Returns `{ errors, values }` where `values` holds timezone-aware ISO strings
 * (Asia/Tashkent, +05:00) or null for an empty optional value.
 */
export function validateWorkSchedule({ plannedStartInput = '', dueInput = '' } = {}, { dueRequired = true } = {}) {
  const errors = {};
  const values = { planned_start_at: null, due_at: null };
  const due = parseTashkentDateTimeInput(dueInput);
  const start = parseTashkentDateTimeInput(plannedStartInput);

  if (due.state === 'empty') {
    if (dueRequired) errors.due_at = 'Укажите срок работы.';
  } else if (due.state === 'invalid') {
    errors.due_at = 'Срок указан неполностью или некорректно.';
  } else {
    values.due_at = due.iso;
  }

  if (start.state === 'invalid') {
    errors.planned_start_at = 'Плановое начало указано неполностью или некорректно.';
  } else if (start.state === 'valid') {
    values.planned_start_at = start.iso;
  }

  if (start.state === 'valid' && due.state === 'valid' && due.epochMs < start.epochMs) {
    errors.due_at = 'Срок не может быть раньше планового начала.';
  }
  return { errors, values };
}

/** Converts a stored work draft (current raw form or the former ISO form) into raw inputs. */
export function workDraftInputs(work = {}) {
  const fromIso = (value) => (typeof value === 'string' && value ? toTashkentDateTimeInput(value) : '');
  return {
    dueInput: typeof work.dueInput === 'string' ? work.dueInput : fromIso(work.due_at),
    plannedStartInput: typeof work.plannedStartInput === 'string' ? work.plannedStartInput : fromIso(work.planned_start_at),
  };
}
