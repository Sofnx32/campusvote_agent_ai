// ============================================
// ESCENARIO 6: RECUPERACIÓN ANTE ERRORES
// ============================================
const { sendMessage, waitForAgentResponse, getLastAssistantMessage } = require('../helpers/chat');
const { checkFriendlyError } = require('../helpers/validators');
const { record } = require('../helpers/evidence');

const errorTests = [
  { input: 'Elimina la feria inexistente XYZ', expect: 'Error amigable' },
  { input: 'Invita a usuario@dominio.com como jurado en feria que no existe', expect: 'Error claro' },
  { input: 'Cambia estado de feria cerrada a abierta', expect: 'Regla de negocio: no permitido' },
];

async function runScenario6(page, config) {
  console.log('\n--- ESCENARIO 6: Recuperación ante errores ---');
  
  for (const test of errorTests) {
    try {
      await sendMessage(page, test.input, config);
      await waitForAgentResponse(page, config);
      const response = await getLastAssistantMessage(page, config);
      
      const { pass } = checkFriendlyError(response);
      
      record('Errores', test.input, pass ? 'PASS' : 'REVIEW',
        'Mensaje de error comprensible sin detalles técnicos',
        `Response: ${response.substring(0,200)}`
      );
    } catch (e) {
      record('Errores', test.input, 'FAIL', 'Respuesta del agente', e.message);
    }
  }
}

module.exports = { runScenario6 };