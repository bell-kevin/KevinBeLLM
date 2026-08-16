// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Exercises static/markdown.js against a minimal DOM shim. The shim only
// implements the handful of node operations the renderer uses, and its
// serializer escapes text, so any test output containing raw markup would prove
// the renderer built an element rather than a text node.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { runInNewContext } from "node:vm";
import test from "node:test";

const VOID_TAGS = new Set(["br", "hr"]);

class TextNode {
  constructor(data) {
    this.data = String(data);
  }

  serialize() {
    return this.data
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }
}

class Element {
  constructor(tagName) {
    this.tagName = String(tagName).toLowerCase();
    this.children = [];
    this.className = "";
    this.attributes = new Map();
  }

  set textContent(value) {
    this.children = [new TextNode(value)];
  }

  set href(value) {
    this.attributes.set("href", value);
  }

  set target(value) {
    this.attributes.set("target", value);
  }

  set rel(value) {
    this.attributes.set("rel", value);
  }

  set start(value) {
    this.attributes.set("start", value);
  }

  append(...nodes) {
    for (const node of nodes) {
      this.children.push(typeof node === "string" ? new TextNode(node) : node);
    }
  }

  serialize() {
    const attributes = [...this.attributes]
      .map(([name, value]) => ` ${name}="${value}"`)
      .join("");
    const classes = this.className ? ` class="${this.className}"` : "";
    if (VOID_TAGS.has(this.tagName)) {
      return `<${this.tagName}${classes}${attributes}>`;
    }
    const inner = this.children.map((child) => child.serialize()).join("");
    return `<${this.tagName}${classes}${attributes}>${inner}</${this.tagName}>`;
  }
}

class Fragment extends Element {
  constructor() {
    super("#fragment");
  }

  serialize() {
    return this.children.map((child) => child.serialize()).join("");
  }
}

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "..", "static", "markdown.js"), "utf8");
const sandbox = {
  URL,
  document: {
    createElement: (tagName) => new Element(tagName),
    createTextNode: (data) => new TextNode(data),
    createDocumentFragment: () => new Fragment(),
  },
};
sandbox.globalThis = sandbox;
runInNewContext(source, sandbox);
const { renderMarkdown, safeExternalUrl } = sandbox.kevinbellmMarkdown;

const render = (markdown) => renderMarkdown(markdown).serialize();

test("emphasis, code, and links become elements rather than literal syntax", () => {
  assert.equal(
    render("Use **bold**, *italic*, and `code` here."),
    '<p class="md-p">Use <strong>bold</strong>, <em>italic</em>, and '
      + '<code class="md-code">code</code> here.</p>',
  );
});

test("the screenshot's bullet syntax renders as a real list", () => {
  assert.equal(
    render("*   **Medical Achievements:** He became the youngest director."),
    '<ul class="md-list"><li><strong>Medical Achievements:</strong>'
      + " He became the youngest director.</li></ul>",
  );
});

test("ordered lists keep their starting number", () => {
  assert.equal(
    render("3. third\n4. fourth"),
    '<ol class="md-list" start="3"><li>third</li><li>fourth</li></ol>',
  );
  assert.equal(
    render("1. first\n2. second"),
    '<ol class="md-list"><li>first</li><li>second</li></ol>',
  );
});

test("indented items nest inside the item above them", () => {
  assert.equal(
    render("- outer\n  - inner\n- sibling"),
    '<ul class="md-list"><li>outer<ul class="md-list"><li>inner</li></ul></li>'
      + "<li>sibling</li></ul>",
  );
});

test("headings are offset so they cannot outrank the page's own headings", () => {
  assert.equal(render("# Title"), '<h3 class="md-heading">Title</h3>');
  assert.equal(render("#### Deep"), '<h6 class="md-heading">Deep</h6>');
  assert.equal(render("###### Deepest"), '<h6 class="md-heading">Deepest</h6>');
});

test("fenced code keeps its contents verbatim and unparsed", () => {
  assert.equal(
    render("```python\nif a < b and **x**:\n    pass\n```"),
    '<pre class="md-pre"><code>if a &lt; b and **x**:\n    pass</code></pre>',
  );
});

test("an unterminated fence still closes at the end of the answer", () => {
  assert.equal(
    render("```\nstill open"),
    '<pre class="md-pre"><code>still open</code></pre>',
  );
});

test("markdown links and bare URLs become safe external anchors", () => {
  assert.equal(
    render("See [Ben Carson](https://en.wikipedia.org/wiki/Ben_Carson) now."),
    '<p class="md-p">See <a class="md-link" href="https://en.wikipedia.org/wiki/Ben_Carson"'
      + ' target="_blank" rel="external noopener noreferrer">Ben Carson</a> now.</p>',
  );
  assert.match(render("https://example.org/a"), /<a class="md-link"/);
});

test("sentence punctuation stays out of a bare URL's href", () => {
  // The system prompt asks the model to cite URLs inline, so a citation that
  // ends a sentence is the common case; a trailing stop inside the href sends
  // the reader to a dead address.
  assert.equal(
    render("See https://en.wikipedia.org/wiki/Ben_Carson."),
    '<p class="md-p">See <a class="md-link" href="https://en.wikipedia.org/wiki/Ben_Carson"'
      + ' target="_blank" rel="external noopener noreferrer">'
      + "https://en.wikipedia.org/wiki/Ben_Carson</a>.</p>",
  );
  assert.match(
    render("Sources https://example.org/a, https://example.org/b!"),
    /href="https:\/\/example\.org\/a".*href="https:\/\/example\.org\/b"/s,
  );
  // A path segment's own dot is not sentence punctuation.
  assert.match(
    render("Read https://example.org/index.html today"),
    /href="https:\/\/example\.org\/index\.html"/,
  );
});

test("emphasis may not open or close on whitespace", () => {
  // Without CommonMark's flanking rule, spaced asterisks in prose italicise
  // everything between them.
  assert.equal(
    render("Compute 2 * 3 and 4 * 5 here."),
    '<p class="md-p">Compute 2 * 3 and 4 * 5 here.</p>',
  );
  assert.equal(
    render("A ** loose ** pair stays literal."),
    '<p class="md-p">A ** loose ** pair stays literal.</p>',
  );
  assert.equal(render("*x*"), '<p class="md-p"><em>x</em></p>');
});

test("links to non-http schemes degrade to plain text", () => {
  assert.equal(
    render("[click](javascript:alert(1))"),
    '<p class="md-p">[click](javascript:alert(1))</p>',
  );
  assert.equal(safeExternalUrl("javascript:alert(1)"), null);
  assert.equal(safeExternalUrl("https://user:pw@example.org/"), null);
});

test("HTML in model output is never parsed as markup", () => {
  assert.equal(
    render("<img src=x onerror=alert(1)>"),
    '<p class="md-p">&lt;img src=x onerror=alert(1)&gt;</p>',
  );
  assert.equal(
    render("- <script>alert(1)</script>"),
    '<ul class="md-list"><li>&lt;script&gt;alert(1)&lt;/script&gt;</li></ul>',
  );
});

test("snake_case survives because single-underscore emphasis is unsupported", () => {
  assert.equal(
    render("call some_helper_name now"),
    '<p class="md-p">call some_helper_name now</p>',
  );
});

test("paragraphs, rules, and quotes separate correctly", () => {
  assert.equal(
    render("one\ntwo\n\nthree"),
    '<p class="md-p">one<br>two</p><p class="md-p">three</p>',
  );
  assert.equal(render("---"), '<hr class="md-rule">');
  assert.equal(
    render("> quoted line"),
    '<blockquote class="md-quote">quoted line</blockquote>',
  );
});

test("a list followed by a paragraph closes the list", () => {
  assert.equal(
    render("- item\n\nAfter the list."),
    '<ul class="md-list"><li>item</li></ul><p class="md-p">After the list.</p>',
  );
});

test("plain prose is unchanged and empty input renders nothing", () => {
  assert.equal(render("Just a sentence."), '<p class="md-p">Just a sentence.</p>');
  assert.equal(render(""), "");
  assert.equal(render("\n\n"), "");
});

test("nested emphasis inside bold still resolves", () => {
  assert.equal(
    render("**see [docs](https://example.org/d)**"),
    '<p class="md-p"><strong>see <a class="md-link" href="https://example.org/d"'
      + ' target="_blank" rel="external noopener noreferrer">docs</a></strong></p>',
  );
});

test("long adversarial input terminates promptly", () => {
  const hostile = `${"*".repeat(5_000)}\n${"`".repeat(5_000)}\n${"[a](".repeat(2_000)}`;
  const started = Date.now();
  render(hostile);
  assert.ok(Date.now() - started < 2_000, "renderer should not backtrack");
});
