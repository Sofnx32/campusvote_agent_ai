// ============================================
// ESCENARIO 7: VALIDACIÓN FRONTEND / UI
// ============================================
const { checkUIElements, isChatVisible } = require('../helpers/validators');
const { record } = require('../helpers/evidence');

async function runScenario7(page, config) {
  console.log('\n--- ESCENARIO 7: Validación frontend ---');
  
  // Verificar elementos UI en desktop
  const uiResults = await checkUIElements(page, config);
  for (const check of uiResults) {
    record('UI', check.name, check.pass ? 'PASS' : 'MISSING', 'Elemento visible', `${check.count} encontrados`);
  }
  
  // Responsive test - móvil
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  const mobileChat = await isChatVisible(page, config);
  record('Responsive', 'Chat en móvil', mobileChat ? 'PASS' : 'FAIL', 'Chat usable en 390px', mobileChat ? 'Visible' : 'No visible');
  
  // Restaurar viewport desktop
  await page.setViewportSize({ width: 1440, height: 900 });
}

module.exports = { runScenario7 };