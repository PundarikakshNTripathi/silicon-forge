// SPDX-License-Identifier: AGPL-3.0-or-later
// Renders src/demo/engineeringNote.md. The author writes that file by hand; this component only formats headings, paragraphs and lists.
import { Fragment } from "react";
import note from "./engineeringNote.md?raw";

export const PLACEHOLDER = "[AUTHOR TO WRITE BY HAND]";

function Line({ text }: { text: string }) {
  const parts = text.split(PLACEHOLDER);
  return <>{parts.map((p, i) => <Fragment key={i}>{p}{i < parts.length - 1 && <mark className="note-todo">{PLACEHOLDER}</mark>}</Fragment>)}</>;
}

export function parseNote(md: string) {
  const blocks: { kind: "h1" | "h2" | "p" | "ul"; text: string[] }[] = [];
  for (const raw of md.split(/\n{2,}/)) {
    const t = raw.trim();
    if (!t) continue;
    if (t.startsWith("## ")) blocks.push({ kind: "h2", text: [t.slice(3)] });
    else if (t.startsWith("# ")) blocks.push({ kind: "h1", text: [t.slice(2)] });
    else if (/^[-*] /.test(t)) blocks.push({ kind: "ul", text: t.split("\n").map((l) => l.replace(/^[-*] /, "")) });
    else blocks.push({ kind: "p", text: [t.replace(/\n/g, " ")] });
  }
  return blocks;
}

export default function EngineeringNote() {
  const blocks = parseNote(note);
  return (
    <>
      <div className="topbar"><span className="title">Engineering note</span></div>
      <div className="page note">
        {blocks.map((b, i) => b.kind === "h1" ? <h1 key={i} className="sr-only">{b.text[0]}</h1>
          : b.kind === "h2" ? <h2 key={i}>{b.text[0]}</h2>
          : b.kind === "ul" ? <ul key={i}>{b.text.map((l, k) => <li key={k}><Line text={l} /></li>)}</ul>
          : <p key={i}><Line text={b.text[0]} /></p>)}
      </div>
    </>
  );
}
