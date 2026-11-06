import { AgentError } from './errors.js';

export const readJsonResponse = async (response, service) => {
  const contentType = response.headers?.get?.('content-type')?.toLowerCase() || '';
  if (!contentType.includes('application/json') && !contentType.includes('+json')) {
    throw new AgentError(
      service === 'python' ? 'AI_SERVICE_UNAVAILABLE' : 'AI_BACKEND_UNAVAILABLE',
      'El servicio conectado devolvió una respuesta no válida.',
      502,
      true,
    );
  }
  const serviceCode = service === 'python' ? 'AI_SERVICE' : 'AI_BACKEND';
  try {
    if (typeof response.text === 'function') {
      const raw = await response.text();
      if (!raw.trim()) throw new AgentError(`${serviceCode}_EMPTY_RESPONSE`, 'El servicio conectado no devolvió contenido.', 502, true);
      return JSON.parse(raw);
    }
    return await response.json();
  } catch (error) {
    if (error instanceof AgentError) throw error;
    throw new AgentError(
      `${serviceCode}_INVALID_RESPONSE`,
      'El servicio conectado devolvió una respuesta no válida.',
      502,
    );
  }
};
