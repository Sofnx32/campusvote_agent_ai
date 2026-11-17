import test from 'node:test';
import assert from 'node:assert/strict';
import { buildOperationResult, fairSummary, sanitizeTechnical } from '../../src/node/views/result-view.js';
import { buildFairListView } from '../../src/node/views/fair-list-view.js';

const backendFair = {
  id: 'fair-1',
  organization_id: 'org-1',
  name: 'Feria Tecnológica',
  description: 'Proyectos de software y robótica',
  image_url: 'https://cdn.example/cover.png',
  status: 'DRAFT',
  starts_at: '2027-05-01T14:00:00.000Z',
  ends_at: '2027-05-02T22:00:00.000Z',
  registration_deadline: null,
  registration_closes_at: '2027-04-30T14:00:00.000Z',
  academic_period: { id: 'period-1', name: '2027-I', start_date: '2027-03-01', end_date: '2027-07-31' },
  public_registration: {
    enabled: true,
    open: true,
    url: 'https://campusvote.example/inscripcion/secret-token',
    enabled_at: '2027-01-01T00:00:00.000Z',
  },
  site: { id: 'site-1', name: 'Sede Central', address: 'Av. 1', city: 'Lima' },
  project_count: 3,
  created_at: '2026-11-15T10:00:00.000Z',
  updated_at: '2026-11-15T10:00:00.000Z',
};

test('createFair renders a friendly fair summary and hides technical fields', () => {
  const { message, ui } = buildOperationResult({
    tool: 'createFair', data: backendFair, proposal: {}, operationId: 'operation-1',
  });

  assert.match(message, /Feria Tecnológica/);
  assert.equal(ui.type, 'success');
  assert.equal(ui.schema.title, '¡Feria creada correctamente!');
  assert.match(ui.schema.description, /borrador/);
  assert.deepEqual(ui.data, {
    kind: 'fair',
    fair_id: 'fair-1',
    name: 'Feria Tecnológica',
    status: 'DRAFT',
    starts_at: '2027-05-01T14:00:00.000Z',
    ends_at: '2027-05-02T22:00:00.000Z',
    academic_period_name: '2027-I',
    project_count: 3,
    site_name: 'Sede Central',
  });
  for (const key of ['id', 'organization_id', 'created_at', 'updated_at', 'image_url', 'registration_closes_at', 'public_registration', 'site', 'academic_period']) {
    assert.equal(key in ui.data, false, `${key} must not be rendered in the conversation`);
  }
  assert.equal(JSON.stringify(ui.data).includes('[object Object]'), false);
  assert.deepEqual(ui.links.map((link) => link.label), ['Ver feria', 'Continuar configurando']);
  assert.equal(ui.links[0].href, '/admin/ferias/fair-1');
  assert.equal(ui.suggestions[0].label, 'Editar feria');
});

test('technical details keep the raw result but redact capability tokens', () => {
  const { ui } = buildOperationResult({
    tool: 'createFair', data: backendFair, proposal: {}, operationId: 'operation-1',
  });

  assert.equal(ui.technical.tool, 'createFair');
  assert.equal(ui.technical.operation_id, 'operation-1');
  assert.equal(ui.technical.result.id, 'fair-1');
  assert.deepEqual(ui.technical.result.public_registration, { enabled: true, open: true });
  assert.equal(ui.technical.result.public_registration.url, undefined);
  assert.equal(JSON.stringify(ui.technical).includes('secret-token'), false);
});

test('fair status changes use natural copy and keep the status badge data', () => {
  const { message, ui } = buildOperationResult({
    tool: 'changeFairStatus',
    data: { ...backendFair, status: 'OPEN' },
    proposal: { fair_id: 'fair-1', status: 'OPEN' },
    operationId: 'operation-2',
  });

  assert.equal(ui.schema.title, 'Estado de la feria actualizado');
  assert.match(ui.schema.description, /abierta/);
  assert.match(message, /abierta/);
  assert.equal(ui.data.status, 'OPEN');
  assert.equal(ui.suggestions[0].label, 'Editar feria');
});

test('closed fairs do not suggest editing through the agent', () => {
  const { ui } = buildOperationResult({
    tool: 'changeFairStatus', data: { ...backendFair, status: 'CLOSED' }, proposal: {}, operationId: 'operation-3',
  });
  assert.deepEqual(ui.suggestions, []);
  assert.deepEqual(ui.links.map((link) => link.label), ['Ver feria']);
});

test('fair updates use editing copy and never expose raw identifiers as text', () => {
  const { message, ui } = buildOperationResult({
    tool: 'updateFair',
    data: { ...backendFair, name: 'Feria Renombrada', starts_at: '2027-06-01T13:00:00.000Z' },
    proposal: { fair_id: 'fair-1', name: 'Feria Renombrada' },
    operationId: 'operation-6',
  });

  assert.equal(ui.schema.title, 'Feria actualizada');
  assert.match(message, /Feria Renombrada/);
  assert.equal(ui.data.name, 'Feria Renombrada');
  assert.equal(ui.data.starts_at, '2027-06-01T13:00:00.000Z');
  assert.equal(ui.links[0].href, '/admin/ferias/fair-1');
  assert.equal(JSON.stringify(ui).includes('[object Object]'), false);
  assert.equal(JSON.stringify(ui.data).includes('2026-11-15'), false);
});

test('rubric criterion results show name, points and description', () => {
  const { ui } = buildOperationResult({
    tool: 'addFairRubricCriterion',
    data: { id: 'criterion-1', name: 'Claridad', description: 'Explica el problema', points: 20, position: 1, created_at: '2026-11-15T10:00:00.000Z' },
    proposal: { fair_id: 'fair-1', name: 'Claridad', points: 20 },
    operationId: 'operation-7',
  });

  assert.equal(ui.schema.title, 'Criterio agregado a la rúbrica');
  assert.deepEqual(ui.data, { name: 'Claridad', description: 'Explica el problema', points: 20, position: 1 });
  assert.equal(ui.links[0].href, '/admin/ferias/fair-1');
});

test('stand results expose only friendly fields and link back to the fair', () => {
  const { message, ui } = buildOperationResult({
    tool: 'createFairStand',
    data: {
      id: 'stand-1', fair_id: 'fair-1', code: 'A-01', description: 'Entrada principal',
      created_at: '2026-11-15T10:00:00.000Z',
    },
    proposal: { fair_id: 'fair-1', code: 'A-01' },
    operationId: 'operation-4',
  });

  assert.equal(ui.schema.title, 'Stand creado');
  assert.deepEqual(ui.data, { code: 'A-01', description: 'Entrada principal' });
  assert.equal(ui.links[0].href, '/admin/ferias/fair-1');
  assert.equal(ui.links[1].href, '/admin/ferias/fair-1?panel=stands');
  assert.match(message, /Stand creado/);
});

test('unknown tools fall back to a generic result without raw nested objects', () => {
  const { ui } = buildOperationResult({
    tool: 'someFutureTool',
    data: { id: 'x', name: 'Criterio', nested: { secret: 'value' }, updated_at: '2026-11-15T10:00:00.000Z' },
    proposal: {},
    operationId: 'operation-5',
  });

  assert.equal(ui.schema.title, 'Operación completada');
  assert.deepEqual(ui.data, { name: 'Criterio' });
  assert.equal(JSON.stringify(ui.data).includes('[object Object]'), false);
  assert.equal(ui.links.length, 0);
});

test('missing or invalid fair data never produces a broken summary', () => {
  assert.equal(fairSummary(null), null);
  assert.equal(fairSummary({ id: 'fair-1' }), null);
  const summary = fairSummary({
    id: 'fair-1', name: 'Feria mínima', status: 'UNKNOWN', starts_at: 'not-a-date', project_count: -1,
  });
  assert.equal(summary.status, null);
  assert.equal(summary.starts_at, null);
  assert.equal(summary.project_count, null);
  assert.equal(summary.academic_period_name, null);
  assert.equal(summary.site_name, null);
});

test('sanitizeTechnical bounds depth and redacts secret keys', () => {
  const sanitized = sanitizeTechnical({
    password: 'plain-text',
    nested: { deeper: { deepest: { value: { tooDeep: true } } } },
  });
  assert.equal(sanitized.password, '[redactado]');
  assert.equal(typeof sanitized.nested, 'object');
  assert.equal(JSON.stringify(sanitized).includes('tooDeep'), false);
});

test('buildFairListView renders empty list with friendly message', () => {
  const { message, ui } = buildFairListView({ fairs: [], tool: 'getSiteFairs', totalCount: 0 });
  assert.equal(ui.type, 'list');
  assert.equal(ui.schema.title, 'Ferias de la sede');
  assert.equal(ui.data.items.length, 0);
  assert.equal(ui.data.total, 0);
  assert.match(message, /No hay ferias/);
});

test('buildFairListView renders multiple fairs with formatted dates and badges', () => {
  const fairs = [
    {
      kind: 'fair',
      fair_id: 'fair-1',
      name: 'Feria de Ciencias',
      status: 'OPEN',
      starts_at: '2027-03-10T14:00:00.000Z',
      ends_at: '2027-03-12T22:00:00.000Z',
      academic_period_name: '2027-I',
      project_count: 15,
      site_name: 'Sede Central',
    },
    {
      kind: 'fair',
      fair_id: 'fair-2',
      name: 'Feria de Robótica',
      status: 'DRAFT',
      starts_at: '2027-05-01T09:00:00.000Z',
      ends_at: '2027-05-03T18:00:00.000Z',
      academic_period_name: '2027-I',
      project_count: 0,
      site_name: 'Sede Norte',
    },
  ];
  const { message, ui } = buildFairListView({ fairs, tool: 'getSiteFairs', totalCount: 2 });
  assert.equal(ui.type, 'list');
  assert.equal(ui.data.items.length, 2);
  assert.equal(ui.data.total, 2);
  assert.match(message, /2 ferias/);
  
  const item1 = ui.data.items[0];
  assert.equal(item1.name, 'Feria de Ciencias');
  assert.equal(item1.status, 'OPEN');
  assert.equal(item1.status_variant, 'success');
  assert.equal(item1.status_label, 'Abierta');
  assert.ok(item1.starts_at_display.includes('marzo'));
  assert.ok(item1.ends_at_display.includes('marzo'));
  assert.equal(item1.project_count, 15);
  
  const item2 = ui.data.items[1];
  assert.equal(item2.name, 'Feria de Robótica');
  assert.equal(item2.status, 'DRAFT');
  assert.equal(item2.status_variant, 'neutral');
  assert.equal(item2.status_label, 'Borrador');
  assert.equal(item2.project_count, 0);
});

test('buildFairListView handles null dates and unknown status gracefully', () => {
  const fairs = [
    {
      kind: 'fair',
      fair_id: 'fair-1',
      name: 'Feria sin fechas',
      status: 'UNKNOWN',
      starts_at: null,
      ends_at: null,
      academic_period_name: null,
      project_count: null,
      site_name: null,
    },
  ];
  const { message, ui } = buildFairListView({ fairs, tool: 'getSiteFairs', totalCount: 1 });
  assert.equal(ui.data.items.length, 1);
  const item = ui.data.items[0];
  assert.equal(item.status_variant, 'info');
  assert.equal(item.status_label, '—');
  assert.equal(item.starts_at_display, null);
  assert.equal(item.project_count, null);
});

test('buildFairListView shows showing count when total exceeds items', () => {
  const fairs = Array(5).fill(null).map((_, i) => ({
    kind: 'fair',
    fair_id: `fair-${i}`,
    name: `Feria ${i}`,
    status: 'OPEN',
    starts_at: '2027-03-10T14:00:00.000Z',
    ends_at: '2027-03-12T22:00:00.000Z',
    academic_period_name: '2027-I',
    project_count: 10,
    site_name: 'Sede Central',
  }));
  const { message, ui } = buildFairListView({ fairs, tool: 'getSiteFairs', totalCount: 50 });
  assert.equal(ui.data.items.length, 5);
  assert.equal(ui.data.total, 50);
  assert.match(message, /mostrando 5/);
});
