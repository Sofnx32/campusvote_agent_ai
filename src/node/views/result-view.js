// Capa de presentación de resultados de escritura.
// El backend devuelve objetos completos (ids, timestamps, nested objects). Esos
// datos siguen disponibles para operaciones internas y para el panel técnico,
// pero la conversación solo recibe un view-model resumido y legible.

const FAIR_STATUS = {
  DRAFT: { label: 'borrador', badge: 'Borrador' },
  OPEN: { label: 'abierta', badge: 'Abierta' },
  CLOSED: { label: 'cerrada', badge: 'Cerrada' },
};

const SUMMARY_LABELS = {
  name: 'Nombre', description: 'Descripción', code: 'Código del stand',
  min_score: 'Puntaje mínimo', max_score: 'Puntaje máximo', points: 'Puntos',
  position: 'Posición',
};

const TECHNICAL_KEYS = new Set([
  'id', 'organization_id', 'site_id', 'fair_id', 'category_id', 'stand_id', 'criterion_id',
  'created_at', 'updated_at', 'registration_closes_at', 'public_registration',
  'academic_period', 'academic_period_id', 'image_url',
]);

const SECRET_KEY = /token|secret|password|authorization|api[_-]?key/i;
const MAX_TECHNICAL_DEPTH = 4;

const text = (value, max = 300) => (
  typeof value === 'string' && value.trim() ? value.trim().slice(0, max) : null
);

const isoDate = (value) => {
  if (typeof value !== 'string' || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : new Date(parsed).toISOString();
};

const count = (value) => (Number.isFinite(value) && value >= 0 ? Math.trunc(value) : null);

const isPrimitive = (value) => value === null || ['string', 'number', 'boolean'].includes(typeof value);

export const fairSummary = (fair) => {
  if (!fair || typeof fair !== 'object' || Array.isArray(fair)) return null;
  const name = text(fair.name);
  if (!name) return null;
  return {
    kind: 'fair', fair_id: text(fair.id, 64), name,
    status: Object.hasOwn(FAIR_STATUS, fair.status) ? fair.status : null,
    starts_at: isoDate(fair.starts_at), ends_at: isoDate(fair.ends_at),
    academic_period_name: text(fair.academic_period?.name),
    project_count: count(fair.project_count),
    site_name: text(fair.site?.name),
  };
};

export const sanitizeTechnical = (value, depth = 0) => {
  if (value === null || isPrimitive(value)) {
    return typeof value === 'string' ? value.slice(0, 2000) : value;
  }
  if (depth >= MAX_TECHNICAL_DEPTH) return '[omitido]';
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => sanitizeTechnical(item, depth + 1));
  if (typeof value !== 'object') return null;
  const sanitized = {};
  for (const [key, item] of Object.entries(value).slice(0, 30)) {
    if (SECRET_KEY.test(key)) { sanitized[key] = '[redactado]'; continue; }
    if (key === 'public_registration' && item && typeof item === 'object') {
      sanitized[key] = { enabled: Boolean(item.enabled), open: Boolean(item.open) }; continue;
    }
    sanitized[key] = sanitizeTechnical(item, depth + 1);
  }
  return sanitized;
};

const fairLinks = (fair) => {
  if (!fair.fair_id) return [];
  const base = `/admin/ferias/${encodeURIComponent(fair.fair_id)}`;
  return [
    { label: 'Ver feria', href: base },
    ...(fair.status !== 'CLOSED' ? [{ label: 'Continuar configurando', href: `${base}?panel=entry` }] : []),
  ];
};

const fairSuggestions = (fair) => (
  fair?.fair_id && fair.status !== 'CLOSED'
    ? [{ label: 'Editar feria', message: `Quiero editar los datos de la feria «${fair.name}».` }]
    : []
);

const operationLinks = (tool, proposal = {}, data = {}) => {
  const fairId = text(proposal.fair_id, 64) || text(data.fair_id, 64);
  if (!fairId) return [];
  const base = `/admin/ferias/${encodeURIComponent(fairId)}`;
  return [
    { label: 'Ver feria', href: base },
    ...(tool === 'createFairStand' || tool === 'updateFairStand' || tool === 'deleteFairStand'
      ? [{ label: 'Ver stands', href: `${base}?panel=stands` }]
      : []),
  ];
};

const FAIR_COPY = {
  createFair: {
    title: '¡Feria creada correctamente!',
    description: (fair) => fair.status === 'DRAFT'
      ? 'La feria se ha registrado como borrador. Puedes revisar su información antes de publicarla.'
      : `La feria quedó en estado ${FAIR_STATUS[fair.status]?.label ?? 'registrada'}.`,
    message: (fair) => `Creé la feria «${fair.name}»${fair.status === 'DRAFT' ? ' como borrador' : ''}.`,
  },
  updateFair: { title: 'Feria actualizada', description: () => 'Los cambios se guardaron correctamente en CampusVote.', message: (fair) => `Actualicé la feria «${fair.name}».` },
  changeFairStatus: { title: 'Estado de la feria actualizado', description: (fair) => `La feria ahora está ${FAIR_STATUS[fair.status]?.label ?? 'actualizada'}.`, message: (fair) => `La feria «${fair.name}» ahora está ${FAIR_STATUS[fair.status]?.label ?? 'actualizada'}.` },
};

const GENERIC_COPY = {
  createFairStand: { title: 'Stand creado', description: 'El stand quedó registrado en la feria.' },
  updateFairStand: { title: 'Stand actualizado', description: 'Los datos del stand se guardaron correctamente.' },
  deleteFairStand: { title: 'Stand eliminado', description: 'El stand se retiró de la feria.' },
  createFairRubric: { title: 'Rúbrica creada', description: 'La rúbrica quedó registrada para la feria.' },
  addFairRubricCriterion: { title: 'Criterio agregado a la rúbrica', description: 'El criterio se sumó a la rúbrica de la feria.' },
  createCategoryFairRubric: { title: 'Rúbrica por categoría creada', description: 'La categoría ya cuenta con su rúbrica.' },
  addCategoryFairRubricCriterion: { title: 'Criterio agregado a la categoría', description: 'El criterio se sumó a la rúbrica de la categoría.' },
  createTeachingCriterion: { title: 'Criterio docente creado', description: 'El criterio de evaluación docente quedó registrado.' },
  updateTeachingCriterion: { title: 'Criterio docente actualizado', description: 'Los cambios del criterio se guardaron correctamente.' },
  toggleTeachingCriterion: { title: 'Estado del criterio actualizado', description: 'El criterio docente cambió de estado.' },
  deleteTeachingCriterion: { title: 'Criterio docente eliminado', description: 'El criterio se retiró de la evaluación docente.' },
};

const GENERIC_FIELDS = {
  stand: ['code', 'description'], rubric: ['name', 'description', 'min_score', 'max_score', 'position'],
  points: ['name', 'description', 'points', 'position'], teaching: ['name', 'description'],
};

const genericFieldNames = (tool) => {
  if (tool === 'createFairStand' || tool === 'updateFairStand' || tool === 'deleteFairStand') return GENERIC_FIELDS.stand;
  if (tool === 'addFairRubricCriterion' || tool === 'addCategoryFairRubricCriterion') return GENERIC_FIELDS.points;
  if (tool?.startsWith('createTeaching') || tool?.startsWith('updateTeaching') || tool?.startsWith('toggleTeaching') || tool?.startsWith('deleteTeaching')) return GENERIC_FIELDS.teaching;
  return GENERIC_FIELDS.rubric;
};

export const genericSummary = (tool, data, proposal = {}) => {
  const source = data && typeof data === 'object' && !Array.isArray(data) ? data : {};
  const values = {};
  for (const key of genericFieldNames(tool)) {
    const value = isPrimitive(source[key]) ? source[key] : isPrimitive(proposal[key]) ? proposal[key] : undefined;
    if (value === undefined || value === null || value === '' || TECHNICAL_KEYS.has(key)) continue;
    values[key] = typeof value === 'string' ? value.slice(0, 300) : value;
  }
  return { data: values, fields: Object.keys(values).map((name) => ({ name, type: 'text', label: SUMMARY_LABELS[name] ?? name.replaceAll('_', ' '), required: false })) };
};

const LIST_TOOL_LABELS = {
  getSiteFairs: { singular: 'feria', plural: 'ferias' },
  getSiteProjects: { singular: 'proyecto', plural: 'proyectos' },
  getSiteStands: { singular: 'stand', plural: 'stands' },
  getSiteResults: { singular: 'resultado', plural: 'resultados' },
  listTeachingCriteria: { singular: 'criterio docente', plural: 'criterios docentes' },
  listFairStands: { singular: 'stand', plural: 'stands' },
  listFairCategories: { singular: 'categoría', plural: 'categorías' },
  listFairCategoryRubrics: { singular: 'rúbrica', plural: 'rúbricas' },
};

const listItemFields = (tool, item) => {
  const base = [];
  if (tool === 'getSiteFairs' || tool === 'listFairCategories' || tool === 'listFairCategoryRubrics') {
    base.push({ name: 'name', type: 'text', label: 'Nombre', required: false });
    base.push({ name: 'description', type: 'textarea', label: 'Descripción', required: false });
  }
  if (tool === 'getSiteFairs') {
    base.push({ name: 'status', type: 'text', label: 'Estado', required: false });
    base.push({ name: 'starts_at', type: 'datetime', label: 'Inicio', required: false });
    base.push({ name: 'ends_at', type: 'datetime', label: 'Fin', required: false });
    base.push({ name: 'project_count', type: 'number', label: 'Proyectos', required: false });
  }
  if (tool === 'getSiteProjects') {
    base.push({ name: 'code', type: 'text', label: 'Código', required: false });
    base.push({ name: 'description', type: 'textarea', label: 'Descripción', required: false });
    base.push({ name: 'fair_name', type: 'text', label: 'Feria', required: false });
  }
  if (tool === 'getSiteStands' || tool === 'listFairStands') {
    base.push({ name: 'code', type: 'text', label: 'Código', required: false });
    base.push({ name: 'description', type: 'textarea', label: 'Descripción', required: false });
    base.push({ name: 'fair_name', type: 'text', label: 'Feria', required: false });
  }
  if (tool === 'listTeachingCriteria') {
    base.push({ name: 'name', type: 'text', label: 'Nombre', required: false });
    base.push({ name: 'description', type: 'textarea', label: 'Descripción', required: false });
    base.push({ name: 'points', type: 'number', label: 'Puntos', required: false });
    base.push({ name: 'is_active', type: 'text', label: 'Estado', required: false });
  }
  return base;
};

const formatListItem = (tool, item) => {
  const formatted = { ...item };
  if (tool === 'getSiteFairs' && item.status) {
    formatted.status = FAIR_STATUS[item.status]?.label ?? item.status;
  }
  if (tool === 'listTeachingCriteria' && typeof item.is_active === 'boolean') {
    formatted.is_active = item.is_active ? 'Activo' : 'Inactivo';
  }
  return formatted;
};

export const buildFairListView = ({ fairs = [], tool, totalCount = 0 } = {}) => {
  const labels = LIST_TOOL_LABELS[tool] || { singular: 'elemento', plural: 'elementos' };
  const count = Number.isFinite(totalCount) ? totalCount : fairs.length;
  const hasItems = fairs.length > 0;
  const message = hasItems
    ? `Se encontraron ${count} ${count === 1 ? labels.singular : labels.plural}.`
    : `No hay ${labels.plural} registradas.`;

  const fields = hasItems ? listItemFields(tool, fairs[0]) : [];
  const data = hasItems ? fairs.map((item) => formatListItem(tool, item)) : [];

  return {
    message,
    ui: {
      type: 'list',
      schema: { title: `Lista de ${labels.plural}`, fields },
      data,
      links: [],
      suggestions: hasItems ? [{ label: `Ver ${labels.singular}`, message: `Quiero ver los detalles de la primera ${labels.singular}.` }] : [],
      technical: { tool: tool ?? 'unknown', returned_count: fairs.length, total_count: count },
      actions: [],
    },
  };
};

export const buildOperationResult = ({ tool, data, proposal = {}, operationId } = {}) => {
  const fair = fairSummary(data);
  const fairTool = tool === 'createFair' || tool === 'updateFair' || tool === 'changeFairStatus';
  const technical = { tool: tool ?? 'unknown', operation_id: text(operationId, 64), completed_at: new Date().toISOString(), result: sanitizeTechnical(data) };

  if (fair && fairTool) {
    const copy = FAIR_COPY[tool];
    return { message: copy.message(fair), ui: { type: 'success', schema: { title: copy.title, description: copy.description(fair), fields: [] }, data: fair, links: fairLinks(fair), suggestions: fairSuggestions(fair), technical, actions: [] } };
  }

  const copy = GENERIC_COPY[tool] ?? { title: 'Operación completada', description: 'CampusVote confirmó la operación.' };
  const summary = genericSummary(tool, data, proposal);
  const label = summary.data.name ?? summary.data.code;
  return { message: label ? `${copy.title}: «${label}».` : `${copy.title} correctamente.`, ui: { type: 'success', schema: { title: copy.title, description: copy.description, fields: summary.fields }, data: summary.data, links: operationLinks(tool, proposal, data), suggestions: [], technical, actions: [] } };
};