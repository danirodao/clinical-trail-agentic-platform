import React from 'react';

type Block =
  | { type: 'paragraph'; text: string }
  | { type: 'list'; ordered: boolean; items: Array<{ text: string; level: number }> }
  | { type: 'heading'; level: 1 | 2 | 3 | 4 | 5 | 6; text: string }
  | { type: 'blockquote'; text: string }
  | { type: 'hr' }
  | { type: 'code'; code: string }
  | { type: 'table'; headers: string[]; rows: string[][] };

function isHorizontalRuleLine(line: string): boolean {
  const trimmed = line.trim();
  return /^(-{3,}|\*{3,}|_{3,})$/.test(trimmed);
}

function parseListLine(line: string): { ordered: boolean; text: string; level: number } | null {
  const bulletMatch = /^(\s*)[-*]\s+(.*)$/.exec(line);
  if (bulletMatch) {
    const indent = bulletMatch[1].length;
    return {
      ordered: false,
      text: bulletMatch[2].trim(),
      level: Math.floor(indent / 2) + 1,
    };
  }

  const orderedMatch = /^(\s*)\d+\.\s+(.*)$/.exec(line);
  if (orderedMatch) {
    const indent = orderedMatch[1].length;
    return {
      ordered: true,
      text: orderedMatch[2].trim(),
      level: Math.floor(indent / 2) + 1,
    };
  }

  return null;
}

function isTableSeparatorLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes('|')) return false;
  const normalized = trimmed.replace(/\|/g, '').trim();
  return /^:?-{3,}:?(\s+:?-{3,}:?)*$/.test(normalized);
}

function parseTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function parseBlocks(content: string): Block[] {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Skip blank lines
    if (!line.trim()) {
      i += 1;
      continue;
    }

    // Fenced code blocks
    if (line.trim().startsWith('```')) {
      i += 1;
      const codeLines: string[] = [];
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length && lines[i].trim().startsWith('```')) i += 1;
      blocks.push({ type: 'code', code: codeLines.join('\n') });
      continue;
    }

    // Horizontal rule
    if (isHorizontalRuleLine(line)) {
      blocks.push({ type: 'hr' });
      i += 1;
      continue;
    }

    // Headings
    const headingMatch = /^(#{1,6})\s+(.*)$/.exec(line.trim());
    if (headingMatch) {
      blocks.push({
        type: 'heading',
        level: headingMatch[1].length as 1 | 2 | 3 | 4 | 5 | 6,
        text: headingMatch[2].trim(),
      });
      i += 1;
      continue;
    }

    // Blockquote (supports multiline contiguous quote lines)
    if (/^\s*>\s?/.test(line)) {
      const quoteLines: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ''));
        i += 1;
      }
      blocks.push({ type: 'blockquote', text: quoteLines.join('\n').trim() });
      continue;
    }

    // Markdown table block: header + separator + rows
    if (
      i + 1 < lines.length &&
      line.includes('|') &&
      isTableSeparatorLine(lines[i + 1])
    ) {
      const headers = parseTableRow(line);
      i += 2; // Skip header + separator

      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(parseTableRow(lines[i]));
        i += 1;
      }

      blocks.push({ type: 'table', headers, rows });
      continue;
    }

    // Bullet or ordered list
    const listLine = parseListLine(line);
    if (listLine) {
      const ordered = listLine.ordered;
      const items: Array<{ text: string; level: number }> = [];
      while (i < lines.length) {
        const parsed = parseListLine(lines[i]);
        if (!parsed || parsed.ordered !== ordered) break;
        items.push({ text: parsed.text, level: parsed.level });
        i += 1;
      }
      blocks.push({ type: 'list', ordered, items });
      continue;
    }

    // Paragraph (collect until blank or another block starter)
    const para: string[] = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith('```') &&
      !isHorizontalRuleLine(lines[i]) &&
      !/^(#{1,6})\s+/.test(lines[i].trim()) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !(i + 1 < lines.length && lines[i].includes('|') && isTableSeparatorLine(lines[i + 1])) &&
      !parseListLine(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }

    blocks.push({ type: 'paragraph', text: para.join('\n') });
  }

  return blocks;
}

interface RichAgentMessageProps {
  content: string;
}

type InlineToken =
  | { type: 'text'; value: string }
  | { type: 'code'; value: string }
  | { type: 'bold'; value: string }
  | { type: 'italic'; value: string }
  | { type: 'link'; label: string; href: string };

function tokenizeInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  const pattern = /(\[[^\]]+\]\((https?:\/\/[^)\s]+)\))|(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    const matchIndex = match.index;
    if (matchIndex > lastIndex) {
      tokens.push({ type: 'text', value: text.slice(lastIndex, matchIndex) });
    }

    const full = match[0];
    if (match[1]) {
      const linkMatch = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(full);
      if (linkMatch) {
        tokens.push({ type: 'link', label: linkMatch[1], href: linkMatch[2] });
      } else {
        tokens.push({ type: 'text', value: full });
      }
    } else if (match[3]) {
      tokens.push({ type: 'code', value: full.slice(1, -1) });
    } else if (match[4]) {
      tokens.push({ type: 'bold', value: full.slice(2, -2) });
    } else if (match[5]) {
      tokens.push({ type: 'italic', value: full.slice(1, -1) });
    } else {
      tokens.push({ type: 'text', value: full });
    }

    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    tokens.push({ type: 'text', value: text.slice(lastIndex) });
  }

  return tokens;
}

function renderInline(text: string): React.ReactNode {
  const tokens = tokenizeInline(text);
  return tokens.map((token, idx) => {
    if (token.type === 'code') {
      return (
        <code
          key={idx}
          className="px-1.5 py-0.5 rounded bg-gray-900 text-gray-100 text-[0.85em]"
        >
          {token.value}
        </code>
      );
    }

    if (token.type === 'bold') {
      return <strong key={idx} className="font-semibold text-gray-900">{token.value}</strong>;
    }

    if (token.type === 'italic') {
      return <em key={idx} className="italic">{token.value}</em>;
    }

    if (token.type === 'link') {
      return (
        <a
          key={idx}
          href={token.href}
          target="_blank"
          rel="noreferrer"
          className="text-blue-700 underline underline-offset-2 hover:text-blue-900"
        >
          {token.label}
        </a>
      );
    }

    return <React.Fragment key={idx}>{token.value}</React.Fragment>;
  });
}

export const RichAgentMessage: React.FC<RichAgentMessageProps> = ({ content }) => {
  const blocks = parseBlocks(content);

  const headingClassMap: Record<number, string> = {
    1: 'text-2xl font-bold text-gray-900',
    2: 'text-xl font-bold text-gray-900',
    3: 'text-lg font-semibold text-gray-900',
    4: 'text-base font-semibold text-gray-900',
    5: 'text-sm font-semibold text-gray-900',
    6: 'text-sm font-medium text-gray-800 uppercase tracking-wide',
  };

  return (
    <div className="space-y-3 text-sm leading-relaxed text-gray-800">
      {blocks.map((block, idx) => {
        if (block.type === 'paragraph') {
          return (
            <p key={idx} className="whitespace-pre-wrap">
              {renderInline(block.text)}
            </p>
          );
        }

        if (block.type === 'heading') {
          return (
            <div key={idx} className={headingClassMap[block.level]}>
              {renderInline(block.text)}
            </div>
          );
        }

        if (block.type === 'blockquote') {
          return (
            <blockquote
              key={idx}
              className="border-l-4 border-blue-200 bg-blue-50/50 px-4 py-2 text-gray-700 italic whitespace-pre-wrap"
            >
              {renderInline(block.text)}
            </blockquote>
          );
        }

        if (block.type === 'hr') {
          return <hr key={idx} className="border-gray-200" />;
        }

        if (block.type === 'list') {
          if (block.ordered) {
            return (
              <ol key={idx} className="list-decimal pl-5 space-y-1">
                {block.items.map((item, itemIdx) => (
                  <li
                    key={itemIdx}
                    className="text-gray-800"
                    style={{ marginLeft: `${(item.level - 1) * 1.25}rem` }}
                  >
                    {renderInline(item.text)}
                  </li>
                ))}
              </ol>
            );
          }

          return (
            <ul key={idx} className="list-disc pl-5 space-y-1">
              {block.items.map((item, itemIdx) => (
                <li
                  key={itemIdx}
                  className="text-gray-800"
                  style={{ marginLeft: `${(item.level - 1) * 1.25}rem` }}
                >
                  {renderInline(item.text)}
                </li>
              ))}
            </ul>
          );
        }

        if (block.type === 'code') {
          return (
            <pre
              key={idx}
              className="overflow-x-auto rounded-lg border border-gray-200 bg-gray-900 text-gray-100 p-3 text-xs"
            >
              <code>{block.code}</code>
            </pre>
          );
        }

        return (
          <div key={idx} className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="min-w-full text-xs">
              <thead className="bg-gray-50">
                <tr>
                  {block.headers.map((header, headerIdx) => (
                    <th
                      key={headerIdx}
                      className="px-3 py-2 text-left font-semibold text-gray-700 border-b border-gray-200"
                    >
                      {renderInline(header)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, rowIdx) => (
                  <tr key={rowIdx} className="odd:bg-white even:bg-gray-50/50">
                    {row.map((cell, cellIdx) => (
                      <td key={cellIdx} className="px-3 py-2 text-gray-700 border-b border-gray-100 align-top">
                        {renderInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
};
