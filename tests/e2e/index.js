// ============================================
// E2E TESTS - Entry point
// ============================================
// Ejecutar: node tests/e2e/runner.js
// O desde package.json: npm run test:e2e

module.exports = {
  // Config
  ...require('./config'),
  // Helpers
  auth: require('./helpers/auth'),
  chat: require('./helpers/chat'),
  validators: require('./helpers/validators'),
  evidence: require('./helpers/evidence'),
  // Scenarios
  scenarios: {
    naturalLanguage: require('./scenarios/01_natural_language'),
    datesTimezone: require('./scenarios/02_dates_timezone'),
    domainOperations: require('./scenarios/03_domain_operations'),
    publicLinks: require('./scenarios/04_public_links'),
    securityPresentation: require('./scenarios/05_security_presentation'),
    errorRecovery: require('./scenarios/06_error_recovery'),
    uiValidation: require('./scenarios/07_ui_validation'),
  },
  // Runner
  run: require('./runner'),
};