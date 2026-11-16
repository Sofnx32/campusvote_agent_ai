// ============================================
// EVIDENCIA Y REPORTES
// ============================================
const fs = require('fs');
const path = require('path');

// Crear directorio de evidencia con timestamp
function createEvidenceDir(baseDir = __dirname) {
  const dir = path.join(baseDir, '..', '..', 'e2e-evidence', Date.now().toString());
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

// Capturar screenshot
async function screenshot(page, evidenceDir, name) {
  const file = path.join(evidenceDir, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

// Guardar evidencia nombrada por escenario/paso
async function saveEvidence(page, evidenceDir, scenario, step, type) {
  const safeScenario = scenario.replace(/\s+/g, '_');
  const safeStep = step.replace(/\s+/g, '_');
  const file = path.join(evidenceDir, `${safeScenario}_${safeStep}_${type}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

// Registrar resultado de prueba
const results = [];
const consoleLogs = [];
const networkLogs = [];

function record(scenario, step, status, expected, actual, evidence = '', severity = 'INFO') {
  results.push({ scenario, step, status, expected, actual, evidence, severity, timestamp: new Date().toISOString() });
  console.log(`[${status}] ${scenario} :: ${step}`);
  if (actual !== expected) console.log(`  Expected: ${expected}`);
  if (actual) console.log(`  Actual: ${actual}`);
  if (evidence) console.log(`  Evidence: ${evidence}`);
}

// Capturar logs de consola y red
function setupLogCapture(page) {
  page.on('console', msg => {
    if (msg.type() === 'error' || msg.type() === 'warning') {
      consoleLogs.push({ type: msg.type(), text: msg.text(), url: page.url() });
    }
  });
  page.on('response', resp => {
    if (resp.status() >= 400) {
      networkLogs.push({ status: resp.status(), url: resp.url() });
    }
  });
}

// Generar reporte final
function generateReport(evidenceDir, environment) {
  const summary = {
    total: results.length,
    passed: results.filter(r => r.status === 'PASS').length,
    failed: results.filter(r => r.status === 'FAIL').length,
    blocked: results.filter(r => r.status === 'BLOCKED').length,
    review: results.filter(r => r.status === 'REVIEW').length,
    critical: results.filter(r => r.severity === 'CRITICAL').length,
    byScenario: {},
  };

  for (const r of results) {
    if (!summary.byScenario[r.scenario]) summary.byScenario[r.scenario] = { pass: 0, fail: 0, blocked: 0, review: 0 };
    summary.byScenario[r.scenario][r.status.toLowerCase()]++;
  }

  const report = {
    generatedAt: new Date().toISOString(),
    environment,
    summary,
    details: results,
    consoleLogs: consoleLogs.slice(0, 50),
    networkErrors: networkLogs.slice(0, 50),
    evidenceDir,
  };

  fs.writeFileSync(path.join(evidenceDir, 'report.json'), JSON.stringify(report, null, 2));
  return { summary, report };
}

// Imprimir resumen en consola
function printSummary(summary) {
  console.log('\n=== RESUMEN ===');
  console.log(`Total pruebas: ${summary.total}`);
  console.log(`✅ PASS: ${summary.passed}`);
  console.log(`❌ FAIL: ${summary.failed}`);
  console.log(`🚫 BLOCKED: ${summary.blocked}`);
  console.log(`⚠️ REVIEW: ${summary.review}`);
  console.log(`\nPor escenario:`);
  for (const [scenario, counts] of Object.entries(summary.byScenario)) {
    console.log(`  ${scenario}: ✅${counts.pass} ❌${counts.fail} 🚫${counts.blocked} ⚠️${counts.review}`);
  }
}

// Determinar si pasa para producción
function isReadyForProd(summary) {
  return summary.failed === 0 && summary.blocked === 0 && summary.critical === 0;
}

module.exports = { 
  createEvidenceDir, 
  screenshot, 
  saveEvidence, 
  record, 
  setupLogCapture, 
  generateReport, 
  printSummary, 
  isReadyForProd,
  results, // exportar para acceso directo si necesario
  consoleLogs,
  networkLogs,
};