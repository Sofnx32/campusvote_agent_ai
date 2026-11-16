// ============================================
// HELPERS DE CHAT / ASISTENTE
// ============================================

// Abrir panel de chat
async function openChat(page, { SELECTORS, TIMEOUTS }) {
  const fab = page.locator(SELECTORS.fabOpen).first();
  await fab.waitFor({ state: 'visible', timeout: TIMEOUTS.default });
  await fab.click();
  await page.waitForSelector(SELECTORS.chatPanel, { timeout: TIMEOUTS.default });
}

// Enviar mensaje al asistente
async function sendMessage(page, message, { SELECTORS, TIMEOUTS }) {
  const input = page.locator(SELECTORS.inputMessage).first();
  await input.waitFor({ state: 'visible', timeout: TIMEOUTS.default });
  await input.fill(message);
  const sendBtn = page.locator(SELECTORS.btnSend).first();
  await sendBtn.click();
  await page.waitForTimeout(TIMEOUTS.messageWait);
}

// Obtener último mensaje del asistente
// IMPORTANTE: Los mensajes están en divs sueltos con data-role="assistant" o .agent-message
// NO dentro de section[aria-label] (esa es la tarjeta del chat completa)
async function getLastAssistantMessage(page, { SELECTORS }) {
  const messages = page.locator(SELECTORS.assistantMsg).all();
  const last = messages[messages.length - 1];
  if (last) return await last.textContent();
  return '';
}

// Esperar a que aparezca respuesta del agente
async function waitForAgentResponse(page, { SELECTORS, TIMEOUTS }, timeout = TIMEOUTS.agentResponse) {
  await page.waitForFunction(
    (sel) => document.querySelectorAll(sel).length > 0,
    SELECTORS.assistantMsg,
    { timeout }
  );
}

// Verificar si el chat está visible (para responsive)
async function isChatVisible(page, { SELECTORS }) {
  return await page.locator(SELECTORS.chatPanel).isVisible();
}

// Iniciar nueva conversación
async function newConversation(page, { SELECTORS }) {
  const btn = page.locator(SELECTORS.btnNewChat).first();
  if (await btn.isVisible()) await btn.click();
}

module.exports = { openChat, sendMessage, getLastAssistantMessage, waitForAgentResponse, isChatVisible, newConversation };