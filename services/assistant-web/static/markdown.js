// SPDX-License-Identifier: AGPL-3.0-or-later

// A deliberately small Markdown subset for rendering finished model answers.
//
// Everything is built with createElement and text nodes: this module never
// assigns innerHTML and never parses markup, so neither the model nor a page
// smuggled in through a tool result can inject HTML. It lives apart from app.js
// so the block and inline grammar can be exercised directly by tests.

"use strict";

(() => {
  // Every branch is bounded and built from negated character classes, so
  // matching stays linear on adversarially long output. Single-underscore
  // emphasis is deliberately unsupported: it would italicise snake_case
  // identifiers, and excluding it also keeps the pattern free of lookbehind,
  // which older Safari cannot compile at all.
  //
  // Emphasis spans may not begin or end on whitespace, which is CommonMark's
  // flanking rule. Without it "2 * 3 and 4 * 5" italicises the text between
  // the asterisks; with it, only a real "*emphasis*" span matches.
  const EMPHASIS_BODY = (delimiter) =>
    `([^${delimiter}\\s\\n](?:[^${delimiter}\\n]{0,1998}[^${delimiter}\\s\\n])?)`;
  const INLINE_SOURCE = [
    "`([^`\\n]{1,2000})`",
    "\\[([^\\]\\n]{1,300})\\]\\((https?:\\/\\/[^)\\s]{1,2048})\\)",
    `\\*\\*${EMPHASIS_BODY("*")}\\*\\*`,
    `__${EMPHASIS_BODY("_")}__`,
    `\\*${EMPHASIS_BODY("*")}\\*`,
    "(https?:\\/\\/[^\\s<>()\\[\\]]{1,2048})",
  ].join("|");
  const HEADING_PATTERN = /^(#{1,6})\s+(.*)$/;
  const UNORDERED_PATTERN = /^(\s*)[-*+]\s+(.*)$/;
  const ORDERED_PATTERN = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
  const RULE_PATTERN = /^\s*([-*_])(\s*\1){2,}\s*$/;
  const QUOTE_PATTERN = /^\s*>\s?(.*)$/;
  const FENCE_PATTERN = /^\s*```/;
  const MAX_INLINE_DEPTH = 4;

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function safeExternalUrl(rawUrl) {
    if (
      typeof rawUrl !== "string"
      || rawUrl.length > 4_096
      || !/^https?:\/\//i.test(rawUrl)
    ) {
      return null;
    }
    try {
      const parsed = new URL(rawUrl);
      if (
        (parsed.protocol !== "http:" && parsed.protocol !== "https:")
        || parsed.username
        || parsed.password
      ) {
        return null;
      }
      return parsed.href;
    } catch {
      return null;
    }
  }

  function markdownLink(url, label) {
    const safeUrl = safeExternalUrl(url);
    if (!safeUrl) {
      return document.createTextNode(label);
    }
    const link = element("a", "md-link", label);
    link.href = safeUrl;
    link.target = "_blank";
    link.rel = "external noopener noreferrer";
    return link;
  }

  function appendInline(parent, text, depth = 0) {
    if (depth >= MAX_INLINE_DEPTH) {
      parent.append(text);
      return;
    }
    // A per-call regex, not a shared one: this function recurses into the
    // contents of bold and italic spans, and a shared /g/ pattern's lastIndex
    // would be rewound by the inner call, leaving the outer loop re-matching
    // the same span forever.
    const pattern = new RegExp(INLINE_SOURCE, "g");
    let cursor = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > cursor) {
        parent.append(text.slice(cursor, match.index));
      }
      cursor = match.index + match[0].length;
      const [, code, linkLabel, linkUrl, boldStar, boldScore, italic, bareUrl] = match;
      if (code !== undefined) {
        parent.append(element("code", "md-code", code));
      } else if (linkUrl !== undefined) {
        parent.append(markdownLink(linkUrl, linkLabel));
      } else if (boldStar !== undefined || boldScore !== undefined) {
        const strong = element("strong");
        appendInline(strong, boldStar ?? boldScore, depth + 1);
        parent.append(strong);
      } else if (italic !== undefined) {
        const emphasis = element("em");
        appendInline(emphasis, italic, depth + 1);
        parent.append(emphasis);
      } else if (bareUrl !== undefined) {
        // Sentence punctuation is not part of the address. The model is asked
        // to cite URLs inline, so "see https://example.org/page." is the common
        // case and the trailing stop would otherwise become part of the href.
        // Rewinding the cursor hands the stripped characters back to the
        // surrounding prose; the scheme prefix keeps the trimmed span long
        // enough that the cursor still advances and the loop cannot stall.
        const trimmed = bareUrl.replace(/[.,;:!?'"]+$/, "");
        cursor = match.index + trimmed.length;
        pattern.lastIndex = cursor;
        parent.append(markdownLink(trimmed, trimmed));
      }
    }
    if (cursor < text.length) {
      parent.append(text.slice(cursor));
    }
  }

  function appendInlineLines(parent, lines) {
    lines.forEach((line, index) => {
      if (index > 0) {
        parent.append(document.createElement("br"));
      }
      appendInline(parent, line);
    });
  }

  function renderMarkdown(text) {
    const fragment = document.createDocumentFragment();
    const lines = String(text).split("\n");
    // Each entry is an open list: the source indent that opened it, the list
    // element itself, and the item that deeper content attaches to.
    const listStack = [];
    let paragraph = [];
    let quote = [];

    const closeParagraph = () => {
      if (paragraph.length === 0) {
        return;
      }
      const block = element("p", "md-p");
      appendInlineLines(block, paragraph);
      paragraph = [];
      (listStack.at(-1)?.item ?? fragment).append(block);
    };

    const closeQuote = () => {
      if (quote.length === 0) {
        return;
      }
      const block = element("blockquote", "md-quote");
      appendInlineLines(block, quote);
      quote = [];
      fragment.append(block);
    };

    const closeLists = (indent = -1) => {
      while (listStack.length > 0 && listStack.at(-1).indent > indent) {
        listStack.pop();
      }
    };

    const closeBlocks = () => {
      closeParagraph();
      closeQuote();
      closeLists();
    };

    const startList = (indent, ordered, start, parent) => {
      const list = element(ordered ? "ol" : "ul", "md-list");
      if (ordered && start !== 1) {
        list.start = start;
      }
      parent.append(list);
      const opened = { indent, ordered, list, item: null };
      listStack.push(opened);
      return opened;
    };

    const openListItem = (indent, ordered, start, content) => {
      closeQuote();
      // A pending paragraph belongs to whatever block is already open, so it is
      // flushed before the stack moves to this line's level.
      closeParagraph();
      closeLists(indent);

      let current = listStack.at(-1);
      if (!current) {
        current = startList(indent, ordered, start, fragment);
      } else if (current.indent < indent) {
        // Deeper indent nests inside the item this list is currently building.
        current = startList(indent, ordered, start, current.item ?? current.list);
      } else if (current.ordered !== ordered) {
        // Same level, different marker: a sibling list, not a continuation.
        listStack.pop();
        current = startList(indent, ordered, start, listStack.at(-1)?.item ?? fragment);
      }

      const item = document.createElement("li");
      appendInline(item, content);
      current.list.append(item);
      current.item = item;
    };

    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];

      if (FENCE_PATTERN.test(line)) {
        closeBlocks();
        const body = [];
        index += 1;
        while (index < lines.length && !FENCE_PATTERN.test(lines[index])) {
          body.push(lines[index]);
          index += 1;
        }
        const pre = element("pre", "md-pre");
        pre.append(element("code", "", body.join("\n")));
        fragment.append(pre);
        continue;
      }

      if (line.trim() === "") {
        closeParagraph();
        closeQuote();
        continue;
      }

      if (RULE_PATTERN.test(line)) {
        closeBlocks();
        fragment.append(element("hr", "md-rule"));
        continue;
      }

      const heading = HEADING_PATTERN.exec(line);
      if (heading) {
        closeBlocks();
        // Offset so a model's "#" cannot outrank the page's own headings.
        const block = element(`h${Math.min(heading[1].length + 2, 6)}`, "md-heading");
        appendInline(block, heading[2]);
        fragment.append(block);
        continue;
      }

      const quoted = QUOTE_PATTERN.exec(line);
      if (quoted) {
        closeParagraph();
        closeLists();
        quote.push(quoted[1]);
        continue;
      }
      closeQuote();

      const ordered = ORDERED_PATTERN.exec(line);
      if (ordered) {
        openListItem(ordered[1].length, true, Number(ordered[2]), ordered[3]);
        continue;
      }

      const unordered = UNORDERED_PATTERN.exec(line);
      if (unordered) {
        openListItem(unordered[1].length, false, 1, unordered[2]);
        continue;
      }

      // An indented plain line under an open list item continues that item.
      const openItem = listStack.at(-1)?.item;
      if (openItem && line.startsWith(" ")) {
        openItem.append(document.createTextNode(" "));
        appendInline(openItem, line.trim());
        continue;
      }
      closeLists();
      paragraph.push(line);
    }

    closeBlocks();
    return fragment;
  }

  globalThis.kevinbellmMarkdown = { renderMarkdown, safeExternalUrl };
})();
