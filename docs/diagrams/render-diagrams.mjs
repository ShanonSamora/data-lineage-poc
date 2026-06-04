// Generador de las figuras del paper (SVG vectorial + PNG).
//
// Uso:
//   cd docs/diagrams
//   npm install @resvg/resvg-js
//   node render-diagrams.mjs
//
// Genera: arquitectura.{svg,png} y modelo-datos.{svg,png}.
// Los SVG son editables (Inkscape/Illustrator); los PNG se insertan en Word.
// Word 2016+ también acepta el SVG nativo (queda vectorial, nítido a cualquier zoom).

import { Resvg } from '@resvg/resvg-js';
import { writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const outDir = dirname(fileURLToPath(import.meta.url));

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const DEFS = `<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="6.5" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L7,3 L0,6 Z" fill="#475569"/></marker></defs>`;

function centerBox(bx, by, bw, bh, fill, stroke, label, fontSize = 12.5, weight = 400, color = '#0F172A') {
  const cx = bx + bw / 2, cy = by + bh / 2;
  const lines = label.split('\n');
  const startY = lines.length === 1 ? cy + 4 : cy - 5;
  const tspans = lines.map((ln, i) => `<tspan x="${cx}" dy="${i === 0 ? 0 : 16}">${esc(ln)}</tspan>`).join('');
  return `<rect x="${bx}" y="${by}" width="${bw}" height="${bh}" rx="8" fill="${fill}" stroke="${stroke}" stroke-width="1.5"/>`
       + `<text x="${cx}" y="${startY}" text-anchor="middle" font-size="${fontSize}" font-weight="${weight}" fill="${color}">${tspans}</text>`;
}

function vArrow(x, y1, y2) {
  return `<line x1="${x}" y1="${y1}" x2="${x}" y2="${y2}" stroke="#475569" stroke-width="1.8" marker-end="url(#arrow)"/>`;
}
function polyArrow(points) {
  return `<polyline points="${points}" fill="none" stroke="#475569" stroke-width="1.8" marker-end="url(#arrow)"/>`;
}
function dashArrow(x1, y1, x2, y2) {
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#475569" stroke-width="1.6" stroke-dasharray="5,4" marker-end="url(#arrow)"/>`;
}

// UML-style class box with colored header and left-aligned monospace fields.
function classBox(x, y, w, { title, stereotype = null, lines, header, body, border }) {
  const r = 10, headerH = stereotype ? 40 : 30, lineH = 20;
  const total = headerH + lines.length * lineH + 14;
  const cx = x + w / 2;
  const p = [];
  p.push(`<rect x="${x}" y="${y}" width="${w}" height="${total}" rx="${r}" fill="${body}" stroke="${border}" stroke-width="1.5"/>`);
  p.push(`<path d="M${x + r},${y} H${x + w - r} A${r},${r} 0 0 1 ${x + w},${y + r} V${y + headerH} H${x} V${y + r} A${r},${r} 0 0 1 ${x + r},${y} Z" fill="${header}"/>`);
  if (stereotype) {
    p.push(`<text x="${cx}" y="${y + 16}" text-anchor="middle" font-size="11" font-style="italic" fill="#E2E8F0">${esc(stereotype)}</text>`);
    p.push(`<text x="${cx}" y="${y + 33}" text-anchor="middle" font-size="14.5" font-weight="700" fill="#FFFFFF">${esc(title)}</text>`);
  } else {
    p.push(`<text x="${cx}" y="${y + 20}" text-anchor="middle" font-size="14.5" font-weight="700" fill="#FFFFFF">${esc(title)}</text>`);
  }
  p.push(`<line x1="${x}" y1="${y + headerH}" x2="${x + w}" y2="${y + headerH}" stroke="${border}" stroke-width="1"/>`);
  let fy = y + headerH + 18;
  for (const ln of lines) {
    p.push(`<text x="${x + 16}" y="${fy}" font-size="12" font-family="Consolas, Menlo, monospace" fill="#0F172A">${esc(ln)}</text>`);
    fy += lineH;
  }
  return { svg: p.join(''), bottom: y + total };
}

function label(x, y, text, anchor = 'middle') {
  return `<text x="${x}" y="${y}" text-anchor="${anchor}" font-size="11" font-style="italic" fill="#475569">${esc(text)}</text>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Figura 1 — Arquitectura
// ─────────────────────────────────────────────────────────────────────────────
function buildArchitecture() {
  const W = 1060, H = 772;
  const layers = [
    { n: 1, title: ['Capa de ingesta'], band: '#EFF6FF', stroke: '#3B82F6', box: '#DBEAFE',
      items: ['Directorio local', 'Repo remoto (Git)', 'Análisis multi-repo', 'Trigger de PR\ny cambios'] },
    { n: 2, title: ['Capa de análisis', 'Motor híbrido'], band: '#ECFDF5', stroke: '#10B981', box: '#D1FAE5',
      items: ['Parser SQL\n(sqlglot)', 'Parser LLM\n(GPT-4.1-mini)', 'Parser ADF\n(JSON)', 'Poda de\nnodos huérfanos'] },
    { n: 3, title: ['Capa de grafo', 'en memoria'], band: '#FFFBEB', stroke: '#F59E0B', box: '#FEF3C7',
      items: ['LineageGraph\n(singleton)', 'merge()\nidempotente', 'BFS: upstream,\ndownstream, impacto'] },
    { n: 4, title: ['Capa de exposición'], band: '#F5F3FF', stroke: '#8B5CF6', box: '#EDE9FE',
      items: ['API REST\n(FastAPI)', 'Flow View + Graph View\n(vis.js)', 'GitHub Action\n(PR check)'] },
  ];
  const s = [];
  s.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="Segoe UI, Arial, sans-serif">`);
  s.push(`<rect width="${W}" height="${H}" fill="#FFFFFF"/>`, DEFS);
  s.push(`<text x="${W / 2}" y="40" text-anchor="middle" font-size="23" font-weight="700" fill="#0F172A">Arquitectura del sistema de Data Lineage Estático</text>`);
  const srcW = 560, srcX = (W - srcW) / 2, srcY = 64, srcH = 40, cxAll = W / 2;
  s.push(centerBox(srcX, srcY, srcW, srcH, '#F1F5F9', '#64748B', 'Repositorios de código fuente  ·  SQL · Python · ADF (JSON)', 13.5, 600));
  s.push(vArrow(cxAll, srcY + srcH, 122));
  const bandX = 40, bandW = 980, bandH = 96, gap = 40, firstTop = 124, compX0 = 280, compX1 = 1010;
  layers.forEach((L, li) => {
    const top = firstTop + li * (bandH + gap), cy = top + bandH / 2;
    s.push(`<rect x="${bandX}" y="${top}" width="${bandW}" height="${bandH}" rx="12" fill="${L.band}" stroke="${L.stroke}" stroke-width="1.5"/>`);
    s.push(`<circle cx="72" cy="${cy}" r="16" fill="${L.stroke}"/>`);
    s.push(`<text x="72" y="${cy + 5}" text-anchor="middle" font-size="15" font-weight="700" fill="#FFFFFF">${L.n}</text>`);
    const tStart = L.title.length === 1 ? cy + 5 : cy - 4;
    s.push(`<text x="98" y="${tStart}" font-size="14.5" font-weight="700" fill="#1E293B">${L.title.map((ln, i) => `<tspan x="98" dy="${i === 0 ? 0 : 17}">${esc(ln)}</tspan>`).join('')}</text>`);
    const n = L.items.length, g = n === 4 ? 19 : 17, bw = (compX1 - compX0 - g * (n - 1)) / n, bh = 52, by = top + (bandH - bh) / 2;
    L.items.forEach((it, i) => s.push(centerBox(compX0 + i * (bw + g), by, bw, bh, L.box, L.stroke, it, 12.5, 500)));
    if (li < layers.length - 1) s.push(vArrow(cxAll, top + bandH, top + bandH + gap - 2));
  });
  const lastBottom = firstTop + 3 * (bandH + gap) + bandH, oW = 300, oH = 48, oY = 686, o1X = 210, o2X = 550;
  const o1c = o1X + oW / 2, o2c = o2X + oW / 2;
  s.push(`<line x1="${cxAll}" y1="${lastBottom}" x2="${cxAll}" y2="664" stroke="#475569" stroke-width="1.8"/>`);
  s.push(`<line x1="${o1c}" y1="664" x2="${o2c}" y2="664" stroke="#475569" stroke-width="1.8"/>`);
  s.push(vArrow(o1c, 664, oY), vArrow(o2c, 664, oY));
  s.push(centerBox(o1X, oY, oW, oH, '#F1F5F9', '#64748B', 'Exploración interactiva\n(Flow View / Graph View)', 12.5, 600));
  s.push(centerBox(o2X, oY, oW, oH, '#F1F5F9', '#64748B', 'Impacto de lineage en cada PR\n(comentario automático)', 12.5, 600));
  s.push(`</svg>`);
  return s.join('\n');
}

// ─────────────────────────────────────────────────────────────────────────────
// Figura 2 — Modelo de datos
// ─────────────────────────────────────────────────────────────────────────────
function buildDataModel() {
  const W = 1080, H = 636, cxAll = 540;
  const s = [];
  s.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="Segoe UI, Arial, sans-serif">`);
  s.push(`<rect width="${W}" height="${H}" fill="#FFFFFF"/>`, DEFS);
  s.push(`<text x="${cxAll}" y="38" text-anchor="middle" font-size="23" font-weight="700" fill="#0F172A">Modelo de datos del grafo de linaje</text>`);

  // LineageGraph (container)
  const g = classBox(350, 72, 380, {
    title: 'LineageGraph',
    lines: ['nodes : list[LineageNode]', 'edges : list[LineageEdge]', 'merge(other)  ·  to_dict()'],
    header: '#475569', body: '#F8FAFC', border: '#475569',
  });
  s.push(g.svg);
  // composition diamond + split arrows to Node / Edge
  s.push(`<path d="M${cxAll},${g.bottom} L${cxAll + 8},${g.bottom + 7} L${cxAll},${g.bottom + 14} L${cxAll - 8},${g.bottom + 7} Z" fill="#475569"/>`);
  const dY = g.bottom + 14;
  s.push(polyArrow(`${cxAll},${dY} ${cxAll},210 300,210 300,250`));
  s.push(polyArrow(`${cxAll},${dY} ${cxAll},210 780,210 780,250`));
  s.push(label(420, 204, 'nodes : 1..*'));
  s.push(label(660, 204, 'edges : 1..*'));

  // LineageNode
  const node = classBox(150, 250, 300, {
    title: 'LineageNode',
    lines: ['id : str', 'name : str', 'node_type : NodeType', 'source_repo : str', 'metadata : dict'],
    header: '#2563EB', body: '#EFF6FF', border: '#2563EB',
  });
  s.push(node.svg);

  // LineageEdge
  const edge = classBox(620, 250, 320, {
    title: 'LineageEdge',
    lines: ['source_id : str', 'target_id : str', 'edge_type : EdgeType', 'transformation : str', 'confidence : float', 'metadata : dict'],
    header: '#7C3AED', body: '#F5F3FF', border: '#7C3AED',
  });
  s.push(edge.svg);

  // edge -> node reference (by id)
  s.push(dashArrow(620, 330, 452, 330));
  s.push(label(536, 323, 'source_id / target_id  →  id'));

  // node_type / edge_type -> enums
  s.push(vArrow(300, node.bottom, 454));
  s.push(label(316, 424, 'node_type', 'start'));
  s.push(vArrow(780, edge.bottom, 454));
  s.push(label(796, 437, 'edge_type', 'start'));

  // NodeType enum
  s.push(classBox(150, 454, 300, {
    title: 'NodeType', stereotype: '«enum»',
    lines: ['TABLE,  VIEW,  COLUMN,', 'PROCEDURE,  PYTHON_FUNCTION,', 'FILE,  ADF_PIPELINE,', 'ADF_DATASET,  ADF_DATAFLOW'],
    header: '#3B82F6', body: '#EFF6FF', border: '#3B82F6',
  }).svg);

  // EdgeType enum
  s.push(classBox(620, 454, 320, {
    title: 'EdgeType', stereotype: '«enum»',
    lines: ['HAS_COLUMN,  DERIVES_FROM,', 'READS_FROM,  WRITES_TO,', 'DEFINED_IN,  COPIES_TO,', 'TRIGGERS'],
    header: '#8B5CF6', body: '#F5F3FF', border: '#8B5CF6',
  }).svg);

  // caption
  s.push(`<text x="${cxAll}" y="610" text-anchor="middle" font-size="11" fill="#64748B">Cada LineageEdge referencia dos LineageNode por su id.   confidence = 1.0 → determinístico  ·  &lt; 1.0 → inferido por LLM</text>`);
  s.push(`</svg>`);
  return s.join('\n');
}

const diagrams = [
  { name: 'arquitectura', svg: buildArchitecture() },
  { name: 'modelo-datos', svg: buildDataModel() },
];
for (const d of diagrams) {
  writeFileSync(`${outDir}/${d.name}.svg`, d.svg, 'utf-8');
  const r = new Resvg(d.svg, { fitTo: { mode: 'zoom', value: 2 }, font: { loadSystemFonts: true, defaultFontFamily: 'Segoe UI' }, background: 'white' });
  writeFileSync(`${outDir}/${d.name}.png`, r.render().asPng());
  console.log('OK ', d.name + '.svg /', d.name + '.png');
}
