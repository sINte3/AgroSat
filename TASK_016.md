# TASK_016 — Render markdown in AI recommendation output

## Skills to load
- `/mnt/skills/user/full-output-enforcement/SKILL.md`

## Problem

The AI recommendation from Claude returns markdown (### headers, **bold**, numbered lists, - bullets),
but it's displayed as raw text with visible `###` and `**` symbols. We need to render it as formatted HTML.

## Solution: Lightweight inline markdown renderer (no external library)

In the file where the AI result is rendered (likely `frontend/src/pages/EnterpriseDetailPage.jsx`),
replace the plain `{aiResults[alert.id]}` text output with a small markdown parser.

### Step 1: Add a helper function at the top of the file (after imports)

```jsx
// Lightweight markdown → React renderer for AI recommendations
function renderMarkdown(text) {
  if (!text) return null;

  const lines = text.split('\n');
  const elements = [];
  let listItems = [];
  let listType = null; // 'ul' or 'ol'

  const flushList = (key) => {
    if (listItems.length === 0) return;
    if (listType === 'ol') {
      elements.push(
        <ol key={`ol-${key}`} style={{ margin: '6px 0', paddingLeft: 20, color: '#c8e6c9' }}>
          {listItems}
        </ol>
      );
    } else {
      elements.push(
        <ul key={`ul-${key}`} style={{ margin: '6px 0', paddingLeft: 20, color: '#c8e6c9' }}>
          {listItems}
        </ul>
      );
    }
    listItems = [];
    listType = null;
  };

  // Inline bold parser: **text** → <strong>
  const parseInline = (str) => {
    const parts = str.split(/(\*\*[^*]+\*\*)/g);
    return parts.map((part, i) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        return <strong key={i} style={{ color: '#e8f5e9', fontWeight: 700 }}>{part.slice(2, -2)}</strong>;
      }
      // also handle *italic* (single asterisk)
      const italicParts = part.split(/(\*[^*]+\*)/g);
      return italicParts.map((ip, j) => {
        if (ip.startsWith('*') && ip.endsWith('*') && ip.length > 2) {
          return <em key={`${i}-${j}`} style={{ fontStyle: 'italic', color: '#b9dcc0' }}>{ip.slice(1, -1)}</em>;
        }
        return ip;
      });
    });
  };

  lines.forEach((line, idx) => {
    const trimmed = line.trim();

    // Headers: ### Title
    if (trimmed.startsWith('### ')) {
      flushList(idx);
      elements.push(
        <div key={idx} style={{
          fontSize: 14,
          fontWeight: 700,
          color: '#4ade80',
          marginTop: idx === 0 ? 0 : 14,
          marginBottom: 6,
        }}>
          {trimmed.slice(4)}
        </div>
      );
      return;
    }

    if (trimmed.startsWith('## ')) {
      flushList(idx);
      elements.push(
        <div key={idx} style={{
          fontSize: 15,
          fontWeight: 700,
          color: '#4ade80',
          marginTop: idx === 0 ? 0 : 14,
          marginBottom: 6,
        }}>
          {trimmed.slice(3)}
        </div>
      );
      return;
    }

    // Numbered list: 1. text
    const olMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
    if (olMatch) {
      if (listType !== 'ol') flushList(idx);
      listType = 'ol';
      listItems.push(
        <li key={`li-${idx}`} style={{ marginBottom: 4, lineHeight: 1.5 }}>
          {parseInline(olMatch[2])}
        </li>
      );
      return;
    }

    // Bullet list: - text or • text
    const ulMatch = trimmed.match(/^[-•]\s+(.*)/);
    if (ulMatch) {
      if (listType !== 'ul') flushList(idx);
      listType = 'ul';
      listItems.push(
        <li key={`li-${idx}`} style={{ marginBottom: 4, lineHeight: 1.5 }}>
          {parseInline(ulMatch[1])}
        </li>
      );
      return;
    }

    // Empty line
    if (trimmed === '' || trimmed === '---') {
      flushList(idx);
      return;
    }

    // Regular paragraph
    flushList(idx);
    elements.push(
      <p key={idx} style={{ margin: '4px 0', lineHeight: 1.5, color: '#c8e6c9' }}>
        {parseInline(trimmed)}
      </p>
    );
  });

  flushList('final');
  return elements;
}
```

### Step 2: Use it in the AI result card

Find where the AI result is displayed. It currently looks something like:

```jsx
<div style={{
  marginTop: 8,
  background: '#0a1a0a',
  ...
  whiteSpace: 'pre-wrap',
}}>
  <div style={{ ... }}>🤖 AI Анализ (Claude)</div>
  {aiResults[alert.id]}
</div>
```

Replace `{aiResults[alert.id]}` with `{renderMarkdown(aiResults[alert.id])}` and
REMOVE `whiteSpace: 'pre-wrap'` from the container style (the renderer handles formatting now):

```jsx
<div style={{
  marginTop: 8,
  background: '#0a1a0a',
  border: '1px solid #2d4a2d',
  borderRadius: 8,
  padding: '12px 14px',
  fontSize: 13,
  color: '#c8e6c9',
}}>
  <div style={{
    fontSize: 11,
    color: '#4ade80',
    fontWeight: 600,
    marginBottom: 8,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  }}>
    🤖 AI Анализ (Claude)
  </div>
  {renderMarkdown(aiResults[alert.id])}
</div>
```

## Expected result

Instead of raw `### 🔍 Диагноз` and `**bold**`, the output shows:
- Green section headers (Диагноз, Основные риски, etc.)
- Bold text properly bolded
- Numbered and bulleted lists properly indented
- Clean paragraph spacing

## Only touch this file
`frontend/src/pages/EnterpriseDetailPage.jsx` (or wherever the AI result card is rendered)

## Important
- No external markdown library — use the inline renderer above
- All UI text stays in Russian
- Dark theme colors preserved
