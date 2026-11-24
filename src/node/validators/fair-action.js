import crypto from 'node:crypto';
import { AgentError } from '../errors.js';

export const isFairConfirmation = (message) => /\b(confirmo|confirmar|sí confirmo|si confirmo|adelante)\b/i.test(message);

const AFFIRMATIVE_PATTERNS = [
  // Sí / Si (con y sin tilde, con y sin "por favor", con y sin coma)
  /^s[ií](?:\s*,?\s*por\s+favor)?[.!]?$/i,
  // Ok / Okay
  /^ok(ay)?[.!]?$/i,
  // Dale / Va
  /^dale[.!]?$/i,
  /^va[.!]?$/i,
  // De acuerdo
  /^de\s+acuerdo[.!]?$/i,
  // Confirmo / Confirmar / Adelante / Procede / Hazlo
  /^(confirmo|confirmar|adelante|procede|hazlo)[.!]?$/i,
  // Correcto / Exacto / Está bien / Así es
  /^(correcto|exacto|est[aá]\s+bien|as[ií]\s+es)[.!]?$/i,
  // Claro / Por supuesto / Por fii
  /^claro[.!]?$/i,
  /^por\s+supuesto[.!]?$/i,
  /^por\s+fii[.!]?$/i,
  // Esa misma / Esa
  /^esa\s+mism?[ae][.!]?$/i,
  /^esa[.!]?$/i,
  // Perfecto / Bien / De una / Vamos
  /^(perfecto|bien|de\s+una|vamos)[.!]?$/i,
];

export const isExplicitAffirmation = (message) => {
  const text = String(message || '').trim().toLowerCase();
  if (!text || text.length > 60 || text.includes('?')) return false;
  return AFFIRMATIVE_PATTERNS.some(p => p.test(text));
};

export const isFairCancellation = (message) => /\b(cancelar|cancela|no crear|olvídalo|olvidalo)\b/i.test(message);
export const isFairResume = (message) => /\b(continuar|retomar|ver resumen|revisar propuesta)\b/i.test(message);
const allowedFields = new Set(['name', 'description', 'image_url', 'starts_at', 'ends_at', 'registration_deadline']);
const writeFields = {
  createTeachingCriterion: { fields: ['name', 'description'], required: ['name'] },
  updateTeachingCriterion: { fields: ['criterion_id', 'name', 'description'], required: ['criterion_id'] },
  toggleTeachingCriterion: { fields: ['criterion_id'], required: ['criterion_id'] },
  deleteTeachingCriterion: { fields: ['criterion_id'], required: ['criterion_id'] },
  createFairRubric: { fields: ['fair_id', 'name', 'description'], required: ['fair_id', 'name'] },
  addFairRubricCriterion: { fields: ['fair_id', 'name', 'description', 'min_score', 'max_score', 'position'], required: ['fair_id', 'name', 'min_score', 'max_score'] },
  createCategoryFairRubric: { fields: ['fair_id', 'category_id', 'name', 'description'], required: ['fair_id', 'category_id', 'name'] },
  addCategoryFairRubricCriterion: { fields: ['fair_id', 'category_id', 'name', 'description', 'points', 'position'], required: ['fair_id', 'category_id', 'name', 'points'] },
  setup_category_with_rubric: { fields: ['fair_id', 'category_name', 'category_description', 'rubric_name', 'rubric_description', 'criteria'], required: ['fair_id', 'category_name', 'rubric_name', 'criteria'] },
  updateFair: { fields: ['fair_id', 'name', 'description', 'starts_at', 'ends_at'], required: ['fair_id'] },
  changeFairStatus: { fields: ['fair_id', 'status'], required: ['fair_id', 'status'] },
  createFairStand: { fields: ['fair_id', 'code', 'description'], required: ['fair_id', 'code'] },
  updateFairStand: { fields: ['fair_id', 'stand_id', 'code', 'description'], required: ['fair_id', 'stand_id'] },
  deleteFairStand: { fields: ['fair_id', 'stand_id'], required: ['fair_id', 'stand_id'] },
};

const DEFAULT_TIMEZONE = 'America/Lima';
const DATE_FIELDS = ['starts_at', 'ends_at', 'registration_deadline'];
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
// Fecha con hora pero SIN zona horaria (lo que entrega <input type="datetime-local">).
const WALL_CLOCK = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/;
const WITH_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i;
const FIELD_LABEL = { starts_at: 'inicio', ends_at: 'fin', registration_deadline: 'cierre de inscripción' };

const validationError = (field, publicMessage) => new AgentError('AI_INVALID_REQUEST', publicMessage, 400, false, {
  publicMessage,
  details: { field },
});

const isValidDateParts = (year, month, day, hour = 0, minute = 0, second = 0) => {
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59) return false;
  const probe = new Date(Date.UTC(year, month - 1, day));
  return probe.getUTCFullYear() === year && probe.getUTCMonth() === month - 1 && probe.getUTCDate() === day;
};

// Diferencia (ms) entre la hora de pared de `timeZone` y UTC para un instante dado.
const timeZoneOffsetMs = (timestamp, timeZone) => {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date(timestamp));
  const value = (type) => Number(parts.find((part) => part.type === type)?.value);
  const wallClock = Date.UTC(value('year'), value('month') - 1, value('day'), value('hour') % 24, value('minute'), value('second'));
  return wallClock - timestamp;
};

// Convierte "2026-11-20T08:00" (hora local de la organización) a ISO 8601 UTC.
const wallClockToIso = (value, timeZone) => {
  const match = WALL_CLOCK.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute, second = '0', millis = '0'] = match;
  const parts = [year, month, day, hour, minute, second].map(Number);
  if (!isValidDateParts(...parts)) return null;
  const [y, mo, d, h, mi, s] = parts;
  const wallClock = Date.UTC(y, mo - 1, d, h, mi, s, Number(millis.padEnd(3, '0')));
  const guess = wallClock - timeZoneOffsetMs(wallClock, timeZone);
  // Segunda pasada: cubre cambios de horario de verano en el borde.
  return new Date(wallClock - timeZoneOffsetMs(guess, timeZone)).toISOString();
};

const isoWithOffset = (value) => {
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return null;
  return new Date(parsed).toISOString();
};

const normalizeFairDate = (field, raw, timeZone) => {
  const value = raw.trim();
  const dateOnly = DATE_ONLY.exec(value);
  if (dateOnly) {
    const [, year, month, day] = dateOnly.map(Number);
    if (!isValidDateParts(year, month, day)) {
      throw validationError(field, `La fecha de ${FIELD_LABEL[field]} no es válida.`);
    }
    if (field !== 'registration_deadline') {
      throw validationError(
        field,
        `Indica la hora de ${FIELD_LABEL[field]} de la feria, por ejemplo 2026-11-20T08:00:00-05:00.`
      );
    }
    // El cierre de inscripción admite solo el día: se toma el final del día local.
    const endOfDay = wallClockToIso(`${value}T23:59:59`, timeZone);
    if (!endOfDay) throw validationError(field, 'La fecha de cierre de inscripción no es válida.');
    return endOfDay;
  }
  if (WALL_CLOCK.test(value)) {
    const iso = wallClockToIso(value, timeZone);
    if (!iso) throw validationError(field, `La fecha de ${FIELD_LABEL[field]} no es válida.`);
    return iso;
  }
  if (WITH_OFFSET.test(value)) {
    const iso = isoWithOffset(value);
    if (!iso) throw validationError(field, `La fecha de ${FIELD_LABEL[field]} no es válida.`);
    return iso;
  }
  // No se usa Date.parse como comodín: formatos como "November 20, 2026 08:00"
  // se interpretarían en la zona del servidor (UTC) y correrían el instante
  // varias horas. Mejor rechazarlos y pedir el formato ISO.
  throw validationError(
    field,
    `La fecha de ${FIELD_LABEL[field]} no es válida. Usa el formato ISO 8601 con hora y zona, por ejemplo 2026-11-20T08:00:00-05:00.`
  );
};

// Normaliza a ISO 8601 UTC y valida el orden. El backend guarda Timestamptz, así
// que enviar siempre un instante absoluto evita el desfase de zona horaria.
const normalizeFairDates = (proposal, context) => {
  const timeZone = typeof context?.timezone === 'string' && context.timezone ? context.timezone : DEFAULT_TIMEZONE;
  for (const field of DATE_FIELDS) {
    const value = proposal[field];
    if (typeof value === 'string' && value.trim()) {
      proposal[field] = normalizeFairDate(field, value, timeZone);
    }
  }
  const start = proposal.starts_at ? Date.parse(proposal.starts_at) : null;
  const end = proposal.ends_at ? Date.parse(proposal.ends_at) : null;
  const deadline = proposal.registration_deadline ? Date.parse(proposal.registration_deadline) : null;
  if (start !== null && end !== null && start > end) {
    throw validationError('starts_at', 'La fecha de inicio no puede ser posterior a la fecha de fin.');
  }
  if (start !== null && deadline !== null && deadline > start) {
    throw validationError('registration_deadline', 'El cierre de inscripción no puede ser posterior al inicio de la feria.');
  }
  return proposal;
};

export const fairActionScopeKey = (context) => crypto.createHash('sha256')
  .update(`${context.sub}:${context.organization_id}:${context.site_id}:${context.conversation_id || ''}`)
  .digest('hex');

export const fairPayloadDigest = (input) => crypto.createHash('sha256')
  .update(JSON.stringify(Object.fromEntries(Object.entries(input).sort(([left], [right]) => left.localeCompare(right)))))
  .digest('hex');
export const operationDigest = (tool, proposal) => fairPayloadDigest({ tool, proposal });

export const preserveUploadedFairImage = (input, currentProposal) => {
  const { image_url: _modelImageUrl, ...fields } = input;
  if (typeof currentProposal?.image_url === 'string') fields.image_url = currentProposal.image_url;
  return fields;
};

export const validateWriteProposal = (tool, input, context) => {
  if (tool === 'createFair') return validateFairProposal(input, context);
  const contract = writeFields[tool];
  if (!contract || !input || typeof input !== 'object' || Array.isArray(input)) {
    throw new AgentError('AI_TOOL_NOT_FOUND', 'Write action is not allowlisted', 404);
  }
  if (Object.keys(input).some((key) => key !== 'site_id' && !contract.fields.includes(key))) {
    throw new AgentError('AI_INVALID_REQUEST', 'Action contains unsupported fields', 400, false, {
      publicMessage: 'La propuesta contiene datos que no se pueden modificar.',
      details: { field: Object.keys(input).find((key) => key !== 'site_id' && !contract.fields.includes(key)) },
    });
  }
  if (input.site_id !== context.site_id) throw new AgentError('AI_SCOPE_FORBIDDEN', 'Site is not authorized', 403);
  if (contract.required.some((field) => input[field] === undefined) || typeof input.name === 'string' && input.name.trim().length < 3) {
    throw new AgentError('AI_INVALID_REQUEST', 'Action is missing required fields', 400, false, {
      publicMessage: 'Faltan datos obligatorios para completar la operación.',
      details: { field: contract.required.find((field) => input[field] === undefined) || 'name' },
    });
  }
  const numericFields = new Set(['min_score', 'max_score', 'points', 'position']);
  for (const [field, value] of Object.entries(input)) {
    if (field === 'site_id' || value === undefined) continue;
    if (numericFields.has(field) ? typeof value !== 'number' || !Number.isFinite(value) : typeof value !== 'string' && value !== null) {
      throw new AgentError('AI_INVALID_REQUEST', `Invalid field: ${field}`, 400, false, {
        publicMessage: `El campo ${field} no tiene un valor válido.`,
        details: { field },
      });
    }
  }
  if (tool === 'changeFairStatus' && !['DRAFT', 'OPEN', 'CLOSED'].includes(input.status)) {
    throw new AgentError('AI_INVALID_REQUEST', 'Fair status is invalid', 400, false, {
      publicMessage: 'El estado de la feria no es válido.',
      details: { field: 'status' },
    });
  }
  const proposal = Object.fromEntries(Object.entries(input).filter(([key]) => key !== 'site_id'));
  if (tool === 'updateFair') normalizeFairDates(proposal, context);
  return proposal;
};

export const validateFairProposal = (input, context) => {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new AgentError('AI_INVALID_REQUEST', 'Fair data is invalid', 400, false, {
      publicMessage: 'Los datos de la feria no son válidos.',
    });
  }
  if (Object.keys(input).some((key) => !allowedFields.has(key) && key !== 'site_id')) {
    throw new AgentError('AI_INVALID_REQUEST', 'Fair data contains unsupported fields', 400, false, {
      publicMessage: 'La propuesta contiene datos que no se pueden modificar.',
      details: { field: Object.keys(input).find((key) => !allowedFields.has(key) && key !== 'site_id') },
    });
  }
  if (input.site_id !== context.site_id) {
    throw new AgentError('AI_SCOPE_FORBIDDEN', 'Site is not authorized', 403);
  }
  if (typeof input.name !== 'string' || input.name.trim().length < 3 || input.name.trim().length > 200) {
    throw new AgentError('AI_INVALID_REQUEST', 'Fair name must contain 3-200 characters', 400, false, {
      publicMessage: 'El nombre de la feria debe tener entre 3 y 200 caracteres.',
      details: { field: 'name' },
    });
  }
  if (input.image_url !== undefined && (typeof input.image_url !== 'string' || input.image_url.length > 1000 || !/^https?:\/\//i.test(input.image_url))) {
    throw new AgentError('AI_INVALID_REQUEST', 'A valid CampusVote image URL is required', 400, false, {
      publicMessage: 'La portada debe ser una URL de imagen válida de CampusVote.',
      details: { field: 'image_url' },
    });
  }
  for (const field of ['description', 'starts_at', 'ends_at', 'registration_deadline']) {
    if (input[field] !== undefined && input[field] !== null && typeof input[field] !== 'string') {
      throw new AgentError('AI_INVALID_REQUEST', `Invalid field: ${field}`, 400, false, {
        publicMessage: `El campo ${field} no tiene un valor válido.`,
        details: { field },
      });
    }
  }
  const { site_id: _siteId, ...fields } = input;
  const proposal = Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, typeof value === 'string' ? value.trim() : value]));
  return normalizeFairDates(proposal, context);
};
