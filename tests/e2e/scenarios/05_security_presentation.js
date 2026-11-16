// ============================================
// ESCENARIO 5: SEGURIDAD Y PRESENTACIÓN
// ============================================
const { checkForSensitiveData, countResultCards } = require('../helpers/validators');
const { record } = require('../helpers/evidence');

async function runScenario5(page, config) {
  console.log('\n--- ESCENARIO 5: Seguridad y presentación ---');
  
  // Verificar que no hay datos sensibles en toda la conversación
  const allText = await page.textContent('body');
  const sensitiveIssues = checkForSensitiveData(allText);
  
  record('Seguridad', 'Datos sensibles en chat', sensitiveIssues.length === 0 ? 'PASS' : 'FAIL',
    'Sin UUID, secretos, JSON crudo, null, [object Object]',
    sensitiveIssues.length === 0 ? 'Limpio' : `Encontrado: ${sensitiveIssues.join(', ')}`
  );
  
  // Verificar tarjetas de resultado
  const resultCards = await countResultCards(page, config);
  record('Frontend', 'Tarjetas de resultado', resultCards > 0 ? 'PASS' : 'REVIEW',
    'Tarjetas de resultado visibles tras operaciones',
    `${resultCards} tarjetas encontradas`
  );
}

module.exports = { runScenario5 };