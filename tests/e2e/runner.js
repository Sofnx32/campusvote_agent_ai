// ============================================
// E2E TEST RUNNER - Orquestador principal
// ============================================
const { chromium } = require('playwright');
const path = require('path');

const { BASE, API, AGENT, USER, SELECTORS, TIMEOUTS } = require('./config');
const { login, injectSession } = require('./helpers/auth');
const { openChat } = require('./helpers/chat');
const { 
  createEvidenceDir, 
  setupLogCapture, 
  generateReport, 
  printSummary, 
  isReadyForProd 
} = require('./helpers/evidence');

const { runScenario1 } = require('./scenarios/01_natural_language');
const { runScenario2 } = require('./scenarios/02_dates_timezone');
const { runScenario3 } = require('./scenarios/03_domain_operations');
const { runScenario4 } = require('./scenarios/04_public_links');
const { runScenario5 } = require('./scenarios/05_security_presentation');
const { runScenario6 } = require('./scenarios/06_error_recovery');
const { runScenario7 } = require('./scenarios/07_ui_validation');

// Configuración compartida para todos los helpers
function makeConfig(evidenceDir) {
  return { BASE, API, AGENT, USER, SELECTORS, TIMEOUTS, evidenceDir };
}

(async () => {
  console.log('=== INICIANDO VALIDACIÓN E2E CAMPUSVOTE ===');
  
  const evidenceDir = createEvidenceDir(__dirname);
  console.log(`Directorio de evidencia: ${evidenceDir}`);
  
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  
  const config = makeConfig(evidenceDir);
  
  // Capturar logs
  setupLogCapture(page);
  
  // ============================================
  // ESCENARIO 0: LOGIN Y SESIÓN
  // ============================================
  console.log('\n--- ESCENARIO 0: Login y sesión ---');
  try {
    const loggedIn = await login(page, config);
    record('Login', 'Acceso a /login', loggedIn ? 'PASS' : 'BLOCKED', 
      'Llegar a verificación 2FA', loggedIn ? 'OK' : 'Requiere TOTP real');
    
    if (!loggedIn) {
      record('Login', 'Autenticación completa', 'BLOCKED', 
        'Login exitoso con TOTP', 'Requiere código TOTP real del autenticador');
      // TODO: Inyectar sesión mock si se tiene token válido
      // await injectSession(page, { token, refreshToken, user });
    }
  } catch (e) {
    record('Login', 'Excepción', 'FAIL', 'Sin errores', e.message);
  }
  
  // Si no hay login, intentar abrir chat igual (puede haber sesión persistente)
  try {
    await openChat(page, config);
  } catch (e) {
    console.log('No se pudo abrir chat, continuando con pruebas...');
  }
  
  // ============================================
  // EJECUTAR ESCENARIOS
  // ============================================
  await runScenario1(page, config);
  await runScenario2(page, config);
  await runScenario3(page, config);
  await runScenario4(page, config);
  await runScenario5(page, config);
  await runScenario6(page, config);
  await runScenario7(page, config);
  
  // ============================================
  // GENERAR INFORME FINAL
  // ============================================
  console.log('\n=== GENERANDO INFORME ===');
  
  const environment = { frontend: BASE, backend: API, agent: AGENT };
  const { summary, report } = generateReport(evidenceDir, environment);
  
  printSummary(summary);
  
  const readyForProd = isReadyForProd(summary);
  console.log(`\n${readyForProd ? '✅ APROBADO PARA PRODUCCIÓN' : '❌ NO APROBADO - Requiere correcciones'}`);
  console.log(`Evidencia guardada en: ${evidenceDir}`);
  
  await browser.close();
  process.exit(readyForProd ? 0 : 1);
})().catch(async (err) => {
  console.error('ERROR FATAL:', err);
  const fatalDir = createEvidenceDir(__dirname);
  const fs = require('fs');
  fs.writeFileSync(path.join(fatalDir, 'fatal.json'), JSON.stringify({ error: err.message, stack: err.stack }, null, 2));
  process.exit(1);
});

// Exportar record para uso en escenarios
const { record } = require('./helpers/evidence');
module.exports = { record };