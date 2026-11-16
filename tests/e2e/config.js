// ============================================
// CONFIGURACIÓN E2E
// ============================================
const BASE = 'https://campusvote-front.onrender.com';
const API = 'https://campusvote-rg13.onrender.com/api';
const AGENT = 'https://campusvote-agent-node.onrender.com';

const USER = {
  email: 'ricky.ushinahua@tecsup.edu.pe',
  password: 'M@rty12345',
};

const SELECTORS = {
  // Chat panel
  chatPanel: 'section[aria-label="Asistente de sede CampusVote"], [data-testid="agent-chat"]',
  fabOpen: 'button[aria-label="Abrir asistente"], button:has-text("Asistente")',
  inputMessage: '#site-agent-message, textarea[placeholder*="mensaje" i], textarea',
  btnSend: 'button[aria-label="Enviar mensaje"], button:has-text("Enviar")',
  btnNewChat: 'button:has-text("Nueva conversación")',
  
  // Messages
  assistantMsg: '[data-role="assistant"], .agent-message, [class*="assistant"]',
  
  // Result cards
  resultCard: '[data-testid="result-card"], .result-card, section[aria-label*="resultado" i]',
  
  // UI checks
  alert: '[role="alert"]',
};

const TIMEOUTS = {
  default: 10000,
  agentResponse: 15000,
  messageWait: 3000,
};

module.exports = { BASE, API, AGENT, USER, SELECTORS, TIMEOUTS };