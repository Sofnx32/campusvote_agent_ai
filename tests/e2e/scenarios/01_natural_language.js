// ============================================
// ESCENARIO 1: COMPRENSIÓN LENGUAJE NATURAL
// ============================================
const { sendMessage, waitForAgentResponse, getLastAssistantMessage } = require('../helpers/chat');
const { checkForSensitiveData, checkNaturalLanguage } = require('../helpers/validators');
const { record, saveEvidence } = require('../helpers/evidence');

const nlTests = [
  { input: 'Quiero crear una feria de ciencias', expect: 'propone creación', domain: 'ferias' },
  { input: 'Programa la feria para mañana a las 9 de la mañana hasta las 6 de la tarde', expect: 'captura fechas AM/PM', domain: 'ferias' },
  { input: 'Cambia la fecha al próximo lunes a las 14:30', expect: 'interpreta relativo + 24h', domain: 'ferias' },
  { input: 'Agrega un stand A-101 en la feria de ciencias', expect: 'identifica feria + crea stand', domain: 'stands' },
  { input: 'Invita a maria@email.com y carlos@email.com como jurados', expect: 'solo emails, sin IDs', domain: 'jurados' },
  { input: 'Crea una rúbrica para evaluar innovación de 0 a 20 puntos', expect: 'captura criterio + puntajes', domain: 'rubricas' },
  { input: 'Activa el criterio de Claridad expositiva', expect: 'busca por nombre, no ID', domain: 'docentes' },
  { input: 'Compárteme el proyecto de robótica con mis estudiantes', expect: 'URL pública, sin ID técnico', domain: 'proyectos' },
  { input: 'Muéstrame el ranking de la feria de matemáticas', expect: 'consulta resultados', domain: 'reportes' },
];

async function runScenario1(page, config) {
  console.log('\n--- ESCENARIO 1: Comprensión lenguaje natural ---');
  
  for (const test of nlTests) {
    try {
      await sendMessage(page, test.input, config);
      await waitForAgentResponse(page, config);
      const response = await getLastAssistantMessage(page, config);
      
      const issues = checkForSensitiveData(response);
      const pass = issues.length === 0 && response.length > 10;
      
      const evidence = await saveEvidence(page, config.evidenceDir, `NL_${test.domain}`, test.input.substring(0,30), 'screenshot');
      
      record(`NL: ${test.domain}`, test.input, pass ? 'PASS' : 'FAIL', 
        'Respuesta coherente sin datos sensibles', 
        pass ? 'OK' : `Issues: ${issues.join(', ')} | Response: ${response.substring(0,200)}`,
        evidence
      );
    } catch (e) {
      record(`NL: ${test.domain}`, test.input, 'FAIL', 'Respuesta del agente', e.message);
    }
  }
}

module.exports = { runScenario1 };