// ============================================
// VALIDADORES DE CONTENIDO
// ============================================

// Detectar datos sensibles en respuestas
function checkForSensitiveData(text) {
  const issues = [];
  if (/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(text)) issues.push('UUID');
  if (/token|secret|password|authorization|api[_-]?key/i.test(text)) issues.push('Secretos');
  if (/\[object Object\]/i.test(text)) issues.push('[object Object]');
  if (/\{\s*".*":/i.test(text)) issues.push('JSON crudo');
  if (/\bnull\b|\bundefined\b/i.test(text)) issues.push('Valores null/undefined');
  return issues;
}

// Verificar respuesta en lenguaje natural (fechas, etc.)
function checkNaturalLanguage(response) {
  const hasISO = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(response);
  const hasNatural = /(mañana|tarde|AM|PM|\d{1,2}:\d{2}|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)/i.test(response);
  return { hasISO, hasNatural, pass: hasNatural && !hasISO };
}

// Verificar URL pública sin IDs técnicos
function checkPublicLink(response) {
  const hasURL = /https?:\/\//.test(response);
  const hasNoUUID = !/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(response);
  const hasNoProjectId = !/project.?id|projectid/i.test(response);
  return { hasURL, hasNoUUID, hasNoProjectId, pass: hasURL && hasNoUUID && hasNoProjectId };
}

// Verificar error amigable (sin stack traces, códigos internos)
function checkFriendlyError(response) {
  const issues = checkForSensitiveData(response);
  const hasStackTrace = /at\s+\w+\s+\(/.test(response) || /Error:.+\n\s+at/.test(response);
  const hasInternalCode = /\bP\d{4}\b|\b[0-9a-f]{24}\b/i.test(response);
  return { 
    issues, 
    hasStackTrace, 
    hasInternalCode, 
    pass: issues.length === 0 && !hasStackTrace && !hasInternalCode && response.length > 10 
  };
}

// Verificar elementos UI presentes
async function checkUIElements(page, { SELECTORS }) {
  const checks = [
    { selector: SELECTORS.fabOpen, name: 'FAB asistente' },
    { selector: SELECTORS.inputMessage, name: 'Campo mensaje' },
    { selector: SELECTORS.btnSend, name: 'Botón enviar' },
    { selector: SELECTORS.btnNewChat, name: 'Nueva conversación' },
    { selector: SELECTORS.alert, name: 'Alertas de error' },
  ];
  const results = [];
  for (const check of checks) {
    const count = await page.locator(check.selector).count();
    results.push({ ...check, count, pass: count > 0 });
  }
  return results;
}

// Contar tarjetas de resultado
async function countResultCards(page, { SELECTORS }) {
  return await page.locator(SELECTORS.resultCard).count();
}

module.exports = { 
  checkForSensitiveData, 
  checkNaturalLanguage, 
  checkPublicLink, 
  checkFriendlyError, 
  checkUIElements,
  countResultCards 
};