// ============================================
// ESCENARIO 3: OPERACIONES POR DOMINIO
// ============================================
const { sendMessage, waitForAgentResponse, getLastAssistantMessage } = require('../helpers/chat');
const { checkForSensitiveData } = require('../helpers/validators');
const { record } = require('../helpers/evidence');

const domainTests = [
  { domain: 'ferias', input: 'Lista las ferias de mi sede', expect: 'getSiteFairs' },
  { domain: 'stands', input: '¿Qué stands tiene la feria de ciencias?', expect: 'listFairStands' },
  { domain: 'jurados', input: '¿Quiénes son los jurados de la feria de robótica?', expect: 'consulta jurados' },
  { domain: 'rubricas', input: 'Muéstrame las rúbricas de la feria de matemáticas', expect: 'listFairCategoryRubrics' },
  { domain: 'docentes', input: 'Lista los criterios de evaluación docente', expect: 'listTeachingCriteria' },
  { domain: 'proyectos', input: '¿Qué proyectos están inscritos en la feria de física?', expect: 'getSiteProjects' },
  { domain: 'reportes', input: 'Resultados de la feria de química', expect: 'getSiteResults' },
];

async function runScenario3(page, config) {
  console.log('\n--- ESCENARIO 3: Operaciones por dominio ---');
  
  for (const test of domainTests) {
    try {
      await sendMessage(page, test.input, config);
      await waitForAgentResponse(page, config);
      const response = await getLastAssistantMessage(page, config);
      
      const issues = checkForSensitiveData(response);
      const pass = response.length > 20 && issues.length === 0;
      
      record(`Domain: ${test.domain}`, test.input, pass ? 'PASS' : 'FAIL',
        'Respuesta con datos del dominio',
        pass ? 'OK' : `Issues: ${issues.join(', ')} | Response: ${response.substring(0,200)}`
      );
    } catch (e) {
      record(`Domain: ${test.domain}`, test.input, 'FAIL', 'Respuesta del agente', e.message);
    }
  }
}

module.exports = { runScenario3 };