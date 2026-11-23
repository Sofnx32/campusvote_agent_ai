/**
 * Vistas formateadas para categorías y rúbricas (CampusVote)
 * Sigue la misma estructura de fair-list-view.js y result-view.js
 */

/**
 * Resumen de categorías de una feria
 * @param {string} fairName - Nombre de la feria
 * @param {Array} categories - Array de categorías (cada una con id, name, description, rubrics[])
 * @returns {string} Texto formateado en markdown
 */
export function formatCategoriesSummary(fairName, categories = []) {
  if (!categories.length) {
    return `La feria **${fairName}** aún no tiene categorías registradas. Puedes crear una indicando su nombre y descripción (requiere feria en estado \`DRAFT\`).`;
  }

  const lines = categories.map((cat, index) => {
    const desc = cat.description ? ` — ${cat.description}` : '';
    const rubricBadge = cat.rubrics?.length ? '✅ Con rúbrica' : '⚠️ Sin rúbrica';
    return `${index + 1}. **${cat.name}** (${rubricBadge})${desc}`;
  });

  return [
    `### Categorías de ${fairName} (${categories.length})`,
    ...lines,
  ].join('\n');
}

/**
 * Detalle formateado de una rúbrica
 * @param {Object} rubric - Objeto rúbrica con criterios
 * @param {string|null} categoryName - Nombre de la categoría (null si es rúbrica global)
 * @returns {string} Texto formateado en markdown
 */
export function formatRubricDetail(rubric, categoryName = null) {
  const criteria = Array.isArray(rubric?.criteria) ? rubric.criteria : [];
  const sorted = [...criteria].sort((a, b) => (a.position ?? 999) - (b.position ?? 999));
  const activePoints = sorted
    .filter((c) => c.isActive !== false)
    .reduce((acc, c) => acc + Number(c.points || 0), 0);

  const header = categoryName
    ? `### Rúbrica: ${rubric.name} · Categoría **${categoryName}**`
    : `### Rúbrica Global: ${rubric.name}`;

  const rows = sorted.length
    ? sorted.map((c) => {
        const state = c.isActive === false ? ' _(inactivo)_' : '';
        const desc = c.description ? `: ${c.description}` : '';
        return `- **#${c.position ?? '-'} ${c.name}** — **${c.points} pts**${state}${desc}`;
      })
    : ['- _Aún no tiene criterios registrados._'];

  return [
    header,
    `Puntaje activo: **${activePoints} / 20 pts** (${20 - activePoints} pts disponibles)`,
    ...rows,
  ].join('\n');
}

/**
 * Resumen compacto de rúbrica para listados
 * @param {Object} rubric - Objeto rúbrica
 * @returns {string} Línea de resumen
 */
export function formatRubricSummary(rubric) {
  if (!rubric) return '⚠️ Sin rúbrica';
  const criteria = Array.isArray(rubric?.criteria) ? rubric.criteria : [];
  const activePoints = criteria
    .filter((c) => c.isActive !== false)
    .reduce((acc, c) => acc + Number(c.points || 0), 0);
  return `✅ ${rubric.name} (${activePoints}/20 pts)`;
}

/**
 * Formatea lista de criterios para tabla/resumen
 * @param {Array} criteria - Array de criterios
 * @returns {string} Texto formateado
 */
export function formatCriteriaList(criteria = []) {
  if (!criteria.length) return '_Sin criterios registrados._';

  const sorted = [...criteria].sort((a, b) => (a.position ?? 999) - (b.position ?? 999));

  return sorted.map((c, i) => {
    const state = c.isActive === false ? ' ⏸️' : '';
    const desc = c.description ? ` — ${c.description}` : '';
    return `${i + 1}. **${c.name}** (${c.points} pts${state})${desc}`;
  }).join('\n');
}