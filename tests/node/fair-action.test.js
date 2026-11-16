import test from 'node:test';
import assert from 'node:assert/strict';
import { AgentError } from '../../src/node/errors.js';
import {
  fairActionScopeKey,
  fairPayloadDigest,
  isExplicitAffirmation,
  isFairCancellation,
  isFairConfirmation,
  validateWriteProposal,
  validateFairProposal,
  preserveUploadedFairImage,
} from '../../src/node/validators/fair-action.js';

const context = { sub: 'user-a', organization_id: 'org-a', site_id: 'site-a', conversation_id: 'chat-a' };
const valid = {
  name: 'Feria Tecnológica',
  image_url: 'https://cdn.example/cover.png',
  site_id: 'site-a',
  starts_at: '2027-05-01T09:00:00Z',
  ends_at: '2027-05-02T17:00:00Z',
};

test('validates and strips server-derived site data from fair proposal', () => {
  assert.deepEqual(validateFairProposal(valid, context), {
    name: 'Feria Tecnológica',
    image_url: 'https://cdn.example/cover.png',
    starts_at: '2027-05-01T09:00:00.000Z',
    ends_at: '2027-05-02T17:00:00.000Z',
  });
});

test('normalizes fair dates to an absolute UTC instant', () => {
  const proposal = validateFairProposal({
    ...valid,
    starts_at: '2027-05-01T09:00:00-05:00',
    ends_at: '2027-05-02T17:30',
    registration_deadline: '2027-04-30',
  }, { ...context, timezone: 'America/Lima' });
  assert.equal(proposal.starts_at, '2027-05-01T14:00:00.000Z');
  // 17:30 en Lima = 22:30 UTC.
  assert.equal(proposal.ends_at, '2027-05-02T22:30:00.000Z');
  // Una fecha suelta de cierre se toma al final de ese día local.
  assert.equal(proposal.registration_deadline, '2027-05-01T04:59:59.000Z');
});

test('requires a time for the fair start and end dates', () => {
  for (const field of ['starts_at', 'ends_at']) {
    assert.throws(
      () => validateFairProposal({ ...valid, [field]: '2027-05-01' }, context),
      (error) => error.code === 'AI_INVALID_REQUEST'
        && error.details?.field === field
        && /hora/.test(error.publicMessage)
    );
  }
});

test('rejects parseable non-ISO dates instead of guessing their time zone', () => {
  assert.throws(
    () => validateFairProposal({ ...valid, starts_at: 'November 20, 2026 08:00' }, context),
    (error) => error.code === 'AI_INVALID_REQUEST'
      && error.details?.field === 'starts_at'
      && /ISO 8601/.test(error.publicMessage)
  );
});

test('checks date order after normalization and exposes the failing field', () => {
  assert.throws(
    () => validateFairProposal({ ...valid, ends_at: '2027-04-01T00:00:00Z' }, context),
    (error) => error.code === 'AI_INVALID_REQUEST'
      && error.details?.field === 'starts_at'
      && /posterior/.test(error.publicMessage)
  );
});

test('normalizes updateFair dates too', () => {
  assert.deepEqual(validateWriteProposal('updateFair', {
    fair_id: 'fair-a', starts_at: '2027-05-01T09:00', site_id: 'site-a',
  }, { ...context, timezone: 'America/Lima' }), {
    fair_id: 'fair-a', starts_at: '2027-05-01T14:00:00.000Z',
  });
});

for (const [label, input, expected] of [
  ['missing name', { ...valid, name: '' }, 'AI_INVALID_REQUEST'],
  ['invalid date', { ...valid, starts_at: 'tomorrowish' }, 'AI_INVALID_REQUEST'],
  ['date ordering', { ...valid, ends_at: '2027-04-01T00:00:00Z' }, 'AI_INVALID_REQUEST'],
  ['foreign site', { ...valid, site_id: 'site-b' }, 'AI_SCOPE_FORBIDDEN'],
  ['foreign organization field', { ...valid, organization_id: 'org-b' }, 'AI_INVALID_REQUEST'],
]) {
  test(`rejects fair proposal with ${label}`, () => {
    assert.throws(() => validateFairProposal(input, context),
      (error) => error instanceof AgentError && error.code === expected);
  });
}

test('confirmation scope is user, organization and site specific', () => {
  assert.notEqual(fairActionScopeKey(context), fairActionScopeKey({ ...context, site_id: 'site-b' }));
  assert.notEqual(fairActionScopeKey(context), fairActionScopeKey({ ...context, organization_id: 'org-b' }));
  assert.notEqual(fairActionScopeKey(context), fairActionScopeKey({ ...context, sub: 'user-b' }));
  assert.notEqual(fairActionScopeKey(context), fairActionScopeKey({ ...context, conversation_id: 'chat-b' }));
});

test('confirmation digest binds the exact field values and detects edits', () => {
  const original = validateFairProposal(valid, context);
  const modified = validateFairProposal({ ...valid, name: 'Feria distinta' }, context);
  assert.equal(fairPayloadDigest(original), fairPayloadDigest({ ...original }));
  assert.notEqual(fairPayloadDigest(original), fairPayloadDigest(modified));
});

test('recognizes explicit confirmation and cancellation language', () => {
  assert.equal(isFairConfirmation('Sí, confirmo la creación'), true);
  assert.equal(isFairConfirmation('de acuerdo'), false);
  assert.equal(isFairCancellation('Cancela la feria'), true);
});

test('recognizes short affirmations only when they clearly confirm', () => {
  for (const message of ['sí', 'Si', 'sí, por favor', 'ok', 'dale', 'de acuerdo', 'adelante!', 'correcto']) {
    assert.equal(isExplicitAffirmation(message), true, message);
  }
  for (const message of ['no', 'si la feria es mañana', 'sí, pero cambia el nombre', '¿confirmas?', '']) {
    assert.equal(isExplicitAffirmation(message), false, message);
  }
});

test('validates rubric actions without accepting browser or model scope overrides', () => {
  assert.deepEqual(validateWriteProposal('createFairRubric', {
    fair_id: 'fair-a', name: 'Rúbrica base', site_id: 'site-a',
  }, context), { fair_id: 'fair-a', name: 'Rúbrica base' });
  assert.throws(() => validateWriteProposal('addFairRubricCriterion', {
    fair_id: 'fair-a', name: 'Claridad', min_score: 0, max_score: 20,
    site_id: 'site-a', organization_id: 'org-b',
  }, context), (error) => error.code === 'AI_INVALID_REQUEST');
  assert.throws(() => validateWriteProposal('createFairRubric', {
    fair_id: 'fair-a', name: 'Rúbrica base', site_id: 'site-b',
  }, context), (error) => error.code === 'AI_SCOPE_FORBIDDEN');
});

test('does not trust model-supplied fair image URLs and allows a proposal to wait for upload', () => {
  const proposal = validateWriteProposal('createFair', preserveUploadedFairImage({
    name: 'Feria tecnol�gica', image_url: 'https://attacker.example/fake.jpg', site_id: 'site-a',
  }, null), context);
  assert.equal(proposal.image_url, undefined);
  assert.deepEqual(preserveUploadedFairImage({
    name: 'Feria editada', image_url: 'https://model.example/inventada.png', site_id: 'site-a',
  }, { image_url: 'https://campusvote.example/uploads/owned.png' }), {
    name: 'Feria editada', site_id: 'site-a', image_url: 'https://campusvote.example/uploads/owned.png',
  });
  assert.throws(() => validateFairProposal({ ...valid, image_url: 'data:image/png;base64,abc' }, context),
    (error) => error.code === 'AI_INVALID_REQUEST');
});

test('validates category rubric operations against their strict backend contracts', () => {
  assert.deepEqual(validateWriteProposal('createCategoryFairRubric', {
    fair_id: 'fair-a', category_id: 'category-a', name: 'Innovación', site_id: 'site-a',
  }, context), { fair_id: 'fair-a', category_id: 'category-a', name: 'Innovación' });
  assert.deepEqual(validateWriteProposal('addCategoryFairRubricCriterion', {
    fair_id: 'fair-a', category_id: 'category-a', name: 'Impacto', points: 5, site_id: 'site-a',
  }, context), { fair_id: 'fair-a', category_id: 'category-a', name: 'Impacto', points: 5 });
  assert.throws(() => validateWriteProposal('addCategoryFairRubricCriterion', {
    fair_id: 'fair-a', category_id: 'category-a', name: 'Impacto', points: '5', site_id: 'site-a',
  }, context), (error) => error.code === 'AI_INVALID_REQUEST');
  assert.throws(() => validateWriteProposal('createCategoryFairRubric', {
    fair_id: 'fair-a', category_id: 'category-b', name: 'Innovación', site_id: 'site-a', extra: true,
  }, context), (error) => error.code === 'AI_INVALID_REQUEST');
});

test('validates SITE teaching-criterion actions without accepting a scope override', () => {
  assert.deepEqual(validateWriteProposal('createTeachingCriterion', {
    name: 'Claridad docente', description: 'Evalúa la claridad', site_id: 'site-a',
  }, context), { name: 'Claridad docente', description: 'Evalúa la claridad' });
  assert.deepEqual(validateWriteProposal('updateTeachingCriterion', {
    criterion_id: 'criterion-a', name: 'Claridad mejorada', site_id: 'site-a',
  }, context), { criterion_id: 'criterion-a', name: 'Claridad mejorada' });
  assert.throws(() => validateWriteProposal('createTeachingCriterion', {
    name: 'Criterio ajeno', site_id: 'site-b',
  }, context), (error) => error.code === 'AI_SCOPE_FORBIDDEN');
});
