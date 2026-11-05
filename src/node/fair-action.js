import crypto from 'node:crypto';
import { AgentError } from './errors.js';

export const isFairConfirmation = (message) => /\b(confirmo|confirmar|sí confirmo|si confirmo|adelante)\b/i.test(message);
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
  updateFair: { fields: ['fair_id', 'name', 'description', 'starts_at', 'ends_at'], required: ['fair_id'] },
  changeFairStatus: { fields: ['fair_id', 'status'], required: ['fair_id', 'status'] },
  createFairStand: { fields: ['fair_id', 'code', 'description'], required: ['fair_id', 'code'] },
  updateFairStand: { fields: ['fair_id', 'stand_id', 'code', 'description'], required: ['fair_id', 'stand_id'] },
  deleteFairStand: { fields: ['fair_id', 'stand_id'], required: ['fair_id', 'stand_id'] },
};

export const fairActionScopeKey = (context) => crypto.createHash('sha256')
  .update(`${context.sub}:${context.organization_id}:${context.site_id}:${context.conversation_id || ''}`)
  .digest('hex');

export const fairPayloadDigest = (input) => crypto.createHash('sha256')
  .update(JSON.stringify(Object.fromEntries(Object.entries(input).sort(([left], [right]) => left.localeCompare(right)))))
  .digest('hex');
export const operationDigest = (tool, proposal) => fairPayloadDigest({ tool, proposal });

export const validateWriteProposal = (tool, input, context) => {
  if (tool === 'createFair') return validateFairProposal(input, context);
  const contract = writeFields[tool];
  if (!contract || !input || typeof input !== 'object' || Array.isArray(input)) {
    throw new AgentError('AI_TOOL_NOT_FOUND', 'Write action is not allowlisted', 404);
  }
  if (Object.keys(input).some((key) => key !== 'site_id' && !contract.fields.includes(key))) {
    throw new AgentError('AI_INVALID_REQUEST', 'Action contains unsupported fields', 400);
  }
  if (input.site_id !== context.site_id) throw new AgentError('AI_SCOPE_FORBIDDEN', 'Site is not authorized', 403);
  if (contract.required.some((field) => input[field] === undefined) || typeof input.name === 'string' && input.name.trim().length < 3) {
    throw new AgentError('AI_INVALID_REQUEST', 'Action is missing required fields', 400);
  }
  const numericFields = new Set(['min_score', 'max_score', 'points', 'position']);
  for (const [field, value] of Object.entries(input)) {
    if (field === 'site_id' || value === undefined) continue;
    if (numericFields.has(field) ? typeof value !== 'number' || !Number.isFinite(value) : typeof value !== 'string' && value !== null) {
      throw new AgentError('AI_INVALID_REQUEST', `Invalid field: ${field}`, 400);
    }
  }
  if (tool === 'changeFairStatus' && !['DRAFT', 'OPEN', 'CLOSED'].includes(input.status)) {
    throw new AgentError('AI_INVALID_REQUEST', 'Fair status is invalid', 400);
  }
  return Object.fromEntries(Object.entries(input).filter(([key]) => key !== 'site_id'));
};

export const validateFairProposal = (input, context) => {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new AgentError('AI_INVALID_REQUEST', 'Fair data is invalid', 400);
  }
  if (Object.keys(input).some((key) => !allowedFields.has(key) && key !== 'site_id')) {
    throw new AgentError('AI_INVALID_REQUEST', 'Fair data contains unsupported fields', 400);
  }
  if (input.site_id !== context.site_id) {
    throw new AgentError('AI_SCOPE_FORBIDDEN', 'Site is not authorized', 403);
  }
  if (typeof input.name !== 'string' || input.name.trim().length < 3 || input.name.trim().length > 200) {
    throw new AgentError('AI_INVALID_REQUEST', 'Fair name must contain 3-200 characters', 400);
  }
  if (typeof input.image_url !== 'string' || !/^https?:\/\//i.test(input.image_url)) {
    throw new AgentError('AI_INVALID_REQUEST', 'A valid CampusVote image URL is required', 400);
  }
  for (const field of ['description', 'starts_at', 'ends_at', 'registration_deadline']) {
    if (input[field] !== undefined && input[field] !== null && typeof input[field] !== 'string') {
      throw new AgentError('AI_INVALID_REQUEST', `Invalid field: ${field}`, 400);
    }
  }
  for (const field of ['starts_at', 'ends_at', 'registration_deadline']) {
    if (input[field] && Number.isNaN(Date.parse(input[field]))) {
      throw new AgentError('AI_INVALID_REQUEST', `Invalid date: ${field}`, 400);
    }
  }
  const start = input.starts_at ? Date.parse(input.starts_at) : null;
  const end = input.ends_at ? Date.parse(input.ends_at) : null;
  const deadline = input.registration_deadline ? Date.parse(input.registration_deadline) : null;
  if (start !== null && end !== null && start > end) {
    throw new AgentError('AI_INVALID_REQUEST', 'Fair start date must precede its end date', 400);
  }
  if (start !== null && deadline !== null && deadline > start) {
    throw new AgentError('AI_INVALID_REQUEST', 'Registration deadline must not follow the fair start date', 400);
  }
  const { site_id: _siteId, ...fields } = input;
  return Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, typeof value === 'string' ? value.trim() : value]));
};
