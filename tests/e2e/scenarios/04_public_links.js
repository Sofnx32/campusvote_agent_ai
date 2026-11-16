// ============================================
// ESCENARIO 4: PROYECTOS Y ENLACES PÚBLICOS
// ============================================
const { sendMessage, waitForAgentResponse, getLastAssistantMessage } = require('../helpers/chat');
const { checkPublicLink } = require('../helpers/validators');
const { record } = require('../helpers/evidence');

async function runScenario4(page, config) {
  console.log('\n--- ESCENARIO 4: Proyectos y enlaces públicos ---');
  
  try {
    await sendMessage(page, 'Compárteme el proyecto de robótica con mis estudiantes', config);
    await waitForAgentResponse(page, config);
    const response = await getLastAssistantMessage(page, config);
    
    const { hasURL, hasNoUUID, hasNoProjectId, pass } = checkPublicLink(response);
    
    record('Proyectos', 'Compartir proyecto', pass ? 'PASS' : 'FAIL',
      'URL pública sin IDs técnicos',
      `hasURL: ${hasURL}, hasNoUUID: ${hasNoUUID}, hasNoProjectId: ${hasNoProjectId} | Response: ${response.substring(0,300)}`
    );
  } catch (e) {
    record('Proyectos', 'Compartir proyecto', 'FAIL', 'Respuesta del agente', e.message);
  }
}

module.exports = { runScenario4 };