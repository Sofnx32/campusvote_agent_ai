// ============================================
// HELPERS DE AUTENTICACIÓN
// ============================================
const crypto = require('crypto');

// Generar TOTP desde secret base32
function generateTOTP(secret) {
  const base32Decode = (input) => {
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
    const clean = input.toUpperCase().replace(/=+$/, '').replace(/\s+/g, '');
    let bits = 0; let value = 0; const out = [];
    for (const char of clean) {
      const index = alphabet.indexOf(char);
      if (index < 0) throw new Error(`bad base32 char ${char}`);
      value = (value << 5) | index; bits += 5;
      if (bits >= 8) { out.push((value >>> (bits - 8)) & 0xff); bits -= 8; }
    }
    return Buffer.from(out);
  };
  const step = Math.floor(Date.now() / 1000 / 30);
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const hmac = crypto.createHmac('sha1', base32Decode(secret)).update(counter).digest();
  const index = hmac[hmac.length - 1] & 0x0f;
  const code = ((hmac[index] & 0x7f) << 24 | hmac[index + 1] << 16 | hmac[index + 2] << 8 | hmac[index + 3]) % 1_000_000;
  return String(code).padStart(6, '0');
}

// Login con email/password + TOTP
async function login(page, { BASE, USER, TIMEOUTS }) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
  await page.fill('input[type="email"], input[name="email"]', USER.email);
  await page.fill('input[type="password"], input[name="password"]', USER.password);
  await page.click('button[type="submit"]');
  await page.waitForURL('**/login/verificar', { timeout: TIMEOUTS.default });
  return page.url().includes('/login/verificar');
}

// Inyectar sesión existente en localStorage
async function injectSession(page, { token, refreshToken, user }) {
  await page.evaluate(({ token, refreshToken, user }) => {
    localStorage.setItem('cv_access_token', token);
    localStorage.setItem('cv_refresh_token', refreshToken);
    localStorage.setItem('cv_user', JSON.stringify(user));
  }, { token, refreshToken, user });
}

module.exports = { generateTOTP, login, injectSession };