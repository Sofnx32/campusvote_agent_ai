// Vista de listado de ferias para la conversación.
// Transforma los datos crudos del backend en una UI amigable.

import { fairSummary } from './result-view.js';

const FAIR_STATUS = {
  DRAFT: { label: 'borrador', badge: 'Borrador' },
  OPEN: { label: 'abierta', badge: 'Abierta' },
  CLOSED: { label: 'cerrada', badge: 'Cerrada' },
};

const formatDateForDisplay = (isoString) => {
  if (!isoString || typeof isoString !== 'string') return null;
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat('es', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(date);
};

const formatDateOnly = (isoString) => {
  if (!isoString || typeof isoString !== 'string') return null;
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat('es', { dateStyle: 'long', timeZone: 'UTC' }).format(date);
};

/** Detecta si el item ya está resumido (viene de fairSummary o tests) vs raw backend object. */
const isSummarized = (item) => item && typeof item === 'object' && item.kind === 'fair' && item.fair_id;

/** Normaliza a objeto resumido (fairSummary) si viene raw, o usa tal cual si ya está resumido. */
const toSummarized = (item) => isSummarized(item) ? item : fairSummary(item);

/** Construye vista de ferias. Si `onlyDraft=true`, filtra solo DRAFT y adapta mensaje. */
export const buildFairListView = ({ fairs, tool, totalCount, onlyDraft = false } = {}) => {
  const list = Array.isArray(fairs) ? fairs : [];
  const summarized = list.map(toSummarized).filter(Boolean);
  const filtered = onlyDraft ? summarized.filter(f => f.status === 'DRAFT') : summarized;
  const items = filtered;

  if (items.length === 0) {
    const msg = onlyDraft
      ? 'No hay ferias en estado **borrador** en esta sede. Las ferias existentes ya están abiertas o cerradas. ¿Quieres crear una nueva feria?'
      : 'No hay ferias registradas en esta sede.';
    return {
      message: msg,
      ui: {
        type: 'list',
        schema: { title: onlyDraft ? 'Ferias en borrador' : 'Ferias de la sede', description: onlyDraft ? 'Solo se muestran ferias en estado DRAFT para configurar categorías/rúbricas.' : 'No se encontraron ferias.' },
        data: { items: [], total: 0 },
        links: [],
        suggestions: [{ label: 'Crear feria', message: 'Quiero crear una feria nueva.' }],
        technical: { tool: tool ?? 'getSiteFairs', result: { count: 0, onlyDraft } },
        actions: [],
      },
    };
  }

  const total = typeof totalCount === 'number' && totalCount >= 0 ? totalCount : items.length;
  const hasMore = total > items.length;

  const messageLines = [
    `Se encontraron ${total} feria${total !== 1 ? 's' : ''}${hasMore ? ` (mostrando ${items.length})` : ''}.`,
  ];

  // Mensaje conversacional sin IDs: "• **Nombre** (estado, fechas)"
  const pretty = items.map(item => {
    const status = FAIR_STATUS[item.status]?.label ?? item.status ?? '—';
    const dates = item.starts_at && item.ends_at
      ? `, ${formatDateOnly(item.starts_at)}–${formatDateOnly(item.ends_at)}`
      : '';
    return `• **${item.name}** (${status}${dates})`;
  }).join('\n');

  return {
    message: messageLines.join(' ') + '\n\n' + pretty,
    ui: {
      type: 'list',
      schema: { title: onlyDraft ? 'Ferias en borrador' : 'Ferias de la sede', description: onlyDraft ? 'Solo se muestran ferias en estado DRAFT para configurar categorías/rúbricas.' : `Listado de ${total} feria${total !== 1 ? 's' : ''} en la sede activa.` },
      data: {
        items: items.map((item) => ({
          fair_id: item.fair_id, // mantenido para tests y links internos
          name: item.name,
          status: item.status,
          starts_at_display: formatDateOnly(item.starts_at),
          starts_at_full: formatDateForDisplay(item.starts_at),
          ends_at_display: formatDateOnly(item.ends_at),
          ends_at_full: formatDateForDisplay(item.ends_at),
          status_label: FAIR_STATUS[item.status]?.badge ?? '—',
          status_variant: item.status === 'DRAFT' ? 'neutral' : item.status === 'OPEN' ? 'success' : 'info',
          project_count: item.project_count,
        })),
        total,
      },
      links: [],
      suggestions: [{ label: 'Crear feria', message: 'Quiero crear una feria nueva.' }],
      technical: { tool: tool ?? 'getSiteFairs', result: { count: items.length, total, onlyDraft } },
      actions: [],
    },
  };
};