import test from 'node:test';
import assert from 'node:assert/strict';
import { AgentError } from '../../src/node/errors.js';
import {
  fairActionScopeKey,
  fairPayloadDigest,
  isFairCancellation,
  isFairConfirmation,
  validateWriteProposal,
  validateFairProposal,
} from '../../src/node/fair-action.js';

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
    starts_at: '2027-05-01T09:00:00Z',
    ends_at: '2027-05-02T17:00:00Z',
  });
});

for (const [label, input, expected] of [
  ['missing name', { ...valid, name: '' }, 'AI_INVALID_REQUEST'],
  ['missing uploaded image', { ...valid, image_url: undefined }, 'AI_INVALID_REQUEST'],
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
