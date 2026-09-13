import { basicSetup, EditorView } from "codemirror";
import { markdown } from "@codemirror/lang-markdown";
import { useEffect, useRef } from "react";

interface MarkdownEditorProps {
  value: string;
  onChange: (value: string) => void;
  fontSize: number;
  showLineNumbers: boolean;
}

export function MarkdownEditor({
  value,
  onChange,
  fontSize,
  showLineNumbers,
}: MarkdownEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (hostRef.current === null) return;
    const view = new EditorView({
      parent: hostRef.current,
      doc: value,
      extensions: [
        basicSetup,
        markdown(),
        EditorView.lineWrapping,
        EditorView.updateListener.of((update) => {
          if (update.docChanged) onChangeRef.current(update.state.doc.toString());
        }),
        EditorView.theme({
          "&": { height: "100%", fontSize: `${fontSize}px` },
          ".cm-scroller": {
            overflow: "auto",
            fontFamily: '"Microsoft YaHei UI", "PingFang SC", sans-serif',
            lineHeight: "1.9",
          },
          ".cm-content": { padding: "30px 42px", caretColor: "#2f6c5a" },
          ".cm-line": { padding: "0" },
          ".cm-gutters": {
            backgroundColor: "transparent",
            border: "0",
            color: "#b0a89e",
            paddingTop: "30px",
          },
          ".cm-activeLine, .cm-activeLineGutter": { backgroundColor: "#f5f2ec" },
          "&.cm-focused": { outline: "none" },
          ".cm-selectionBackground, ::selection": { backgroundColor: "#dce9e2 !important" },
        }),
      ],
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // A document path is supplied as the component key by App, so recreating
    // here is limited to explicit editor preference changes.
  }, [fontSize, showLineNumbers]);

  useEffect(() => {
    const view = viewRef.current;
    if (view === null || view.state.doc.toString() === value) return;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
  }, [value]);

  return (
    <div
      ref={hostRef}
      className={`markdown-editor codemirror-editor${showLineNumbers ? "" : " hide-line-numbers"}`}
      aria-label="Markdown 编辑器"
    />
  );
}
