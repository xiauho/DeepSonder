import type { ReactNode } from "react";

interface MarkdownPreviewProps {
  content: string;
}

export function MarkdownPreview({ content }: MarkdownPreviewProps) {
  return <article className="markdown-editor markdown-preview">{renderBlocks(content)}</article>;
}

function renderBlocks(content: string): ReactNode[] {
  const lines = content.replaceAll("\r\n", "\n").split("\n");
  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  let code: string[] | null = null;

  const flushParagraph = () => {
    if (paragraph.length > 0) {
      const text = paragraph.join(" ").trim();
      if (text) blocks.push(<p key={`p-${blocks.length}`}>{renderInline(text)}</p>);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list.length > 0) {
      blocks.push(<ul key={`ul-${blocks.length}`}>{list.map((item, index) =>
        <li key={`${index}-${item}`}>{renderInline(item)}</li>)}</ul>);
      list = [];
    }
  };

  for (const line of lines) {
    if (line.startsWith("```")) {
      flushParagraph(); flushList();
      if (code === null) code = [];
      else { blocks.push(<pre key={`code-${blocks.length}`}><code>{code.join("\n")}</code></pre>); code = null; }
      continue;
    }
    if (code !== null) { code.push(line); continue; }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading !== null) {
      flushParagraph(); flushList();
      const level = heading[1]!.length;
      const children = renderInline(heading[2]!);
      if (level === 1) blocks.push(<h1 key={`h-${blocks.length}`}>{children}</h1>);
      else if (level === 2) blocks.push(<h2 key={`h-${blocks.length}`}>{children}</h2>);
      else blocks.push(<h3 key={`h-${blocks.length}`}>{children}</h3>);
      continue;
    }
    const item = /^[-*+]\s+(.+)$/.exec(line);
    if (item !== null) { flushParagraph(); list.push(item[1]!); continue; }
    if (line.startsWith("> ")) {
      flushParagraph(); flushList();
      blocks.push(<blockquote key={`q-${blocks.length}`}>{renderInline(line.slice(2))}</blockquote>);
      continue;
    }
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    flushList(); paragraph.push(line.trim());
  }
  flushParagraph(); flushList();
  if (code !== null) blocks.push(<pre key={`code-${blocks.length}`}><code>{code.join("\n")}</code></pre>);
  return blocks;
}

function renderInline(text: string): ReactNode[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).filter(Boolean).map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    return part;
  });
}
