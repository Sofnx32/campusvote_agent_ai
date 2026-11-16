import { config, httpsDispatcher } from '../config.js';
import { AgentError } from '../errors.js';
import { readJsonResponse } from '../utils/http-response.js';

const withTimeout = (ms, task) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return Promise.resolve().then(() => task(controller.signal)).finally(() => clearTimeout(timer));
};

export function registerHealthRoutes(app) {
  app.get('/health', (_req, res) => res.json({ success: true, service: 'campusvote-agent-node' }));

  app.get('/health/agent', async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    try {
      const upstream = await withTimeout(20000, (signal) => fetch(new URL('/health', config.pythonUrl), {
        signal,
        dispatcher: httpsDispatcher,
        headers: { 'X-Correlation-Id': req.requestId },
      }));
      if (!upstream.ok) throw new AgentError('AI_SERVICE_UNAVAILABLE', 'Python readiness check failed', 503, true);
      const payload = await readJsonResponse(upstream, 'python');
      if (payload?.success !== true || payload?.service !== 'campusvote-agent-python') {
        throw new AgentError('AI_SERVICE_UNAVAILABLE', 'Python readiness response is invalid', 503, true);
      }
      return res.json({ success: true, ready: true, service: 'campusvote-agent-node' });
    } catch (error) {
      console.warn(JSON.stringify({
        event: 'dependency_not_ready', service: 'agent-node', dependency: 'agent-python',
        requestId: req.requestId, cause: error?.name || 'Error',
        status: error?.status || undefined,
      }));
      return res.status(503).json({
        success: false, ready: false,
        error: { code: 'AI_SERVICE_UNAVAILABLE', message: 'El asistente se está iniciando.', requestId: req.requestId },
        requestId: req.requestId,
      });
    }
  });
}