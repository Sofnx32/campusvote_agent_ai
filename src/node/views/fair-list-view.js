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

export const buildFairListView = ({ fairs, tool, totalCount } = {}) => {
  const list = Array.isArray(fairs) ? fairs : [];
  const items = list.map((fair) => fairSummary(fair)).filter(Boolean);

  if (items.length === 0) {
    return {
      message: 'No hay ferias registradas en esta sede.',
      ui: {
        type: 'list',
        schema: { title: 'Ferias de la sede', description: 'No se encontraron ferias.' },
        data: { items: [], total: 0 },
        links: [],
        suggestions: [{ label: 'Crear feria', message: 'Quiero crear una feria nueva.' }],
        technical: { tool: tool ?? 'getSiteFairs', result: { count: 0 } },
        actions: [],
      },
    };
  }

  const total = typeof totalCount === 'number' && totalCount >= 0 ? totalCount : items.length;
  const hasMore = total > items.length;

  const messageLines = [
    `Se encontraron ${total} feria${total !== 1 ? 's' : ''}${hasMore ? ` (mostrando ${items.length})` : ''}.`,
  ];

  return {
    message: messageLines.join(' '),
    ui: {
      type: 'list',
      schema: { title: 'Ferias de la sede', description: `Listado de ${total} feria${total !== 1 ? 's' : ''} en la sede activa.` },
      data: {
        items: items.map((item) => ({
          ...item,
          starts_at_display: formatDateOnly(item.starts_at),
          starts_at_full: formatDateForDisplay(item.starts_at),
          ends_at_display: formatDateOnly(item.ends_at),
          ends_at_full: formatDateForDisplay(item.ends_at),
          status_label: FAIR_STATUS[item.status]?.badge ?? item.status ?? '—',
          status_variant: item.status === 'DRAFT' ? 'neutral' : item.status === 'OPEN' ? 'success' : 'info',
        })),
        total,
      },
      links: [],
      suggestions: [{ label: 'Crear feria', message: 'Quiero crear una feria nueva.' }],
      technical: { tool: tool ?? 'getSiteFairs', result: { count: items.length, total } },
      actions: [],
    },
  };
};