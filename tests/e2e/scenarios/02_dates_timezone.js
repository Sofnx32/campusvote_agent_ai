// ============================================
// ESCENARIO 2: FECHAS Y ZONAS HORARIAS
// ============================================
const { sendMessage, waitForAgentResponse, getLastAssistantMessage } = require('../helpers/chat');
const { checkNaturalLanguage } = require('../helpers/validators');
const { record } = require('../helpers/evidence');

const dateTests = [
  { input: 'La feria empieza el 15 de marzo a las 9 AM y termina el 17 a las 6 PM', expect: 'ISO con zona horaria' },
  { input: 'Inicio a las 14:30, fin a las 18:00', expect: 'Formato 24h' },
  { input: 'Empieza mañana a las 10 de la mañana', expect: 'Fecha relativa calculada' },
  { input: 'Del 20 al 22 de noviembre', expect: 'Rango de fechas' },
  { input: 'El último día de marzo a las 11:59 PM', expect: 'Fin de mes + AM/PM' },
];

async function runScenario2(page, config) {
  console.log('\n--- ESCENARIO 2: Fechas y zonas horarias ---');
  
  for (const test of dateTests) {
    try {
      await sendMessage(page, test.input, config);
      await waitForAgentResponse(page, config);
      const response = await getLastAssistantMessage(page, config);
      
      const { hasNatural, hasISO, pass } = checkNaturalLanguage(response);
      
      record('Fechas', test.input, pass ? 'PASS' : 'REVIEW', 
        'Fechas en lenguaje natural, sin ISO crudo en chat',
        `hasNatural: ${hasNatural}, hasISO: ${hasISO} | Response: ${response.substring(0,200)}`
      );
    } catch (e) {
      record('Fechas', test.input, 'FAIL', 'Respuesta del agente', e.message);
    }
  }
}

module.exports = { runScenario2 };