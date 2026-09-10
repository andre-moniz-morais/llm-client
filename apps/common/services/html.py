"""Producing and sanitising the HTML the frontend renders directly.

The frontend inserts backend responses into the page as-is, so every fragment
that reaches it goes through :func:`sanitize` first.  Model output is treated as
untrusted input: a model can be talked into emitting a ``<script>`` tag or a
``javascript:`` URL, and the sanitiser is what stops that from mattering.

:data:`CHAT_SYSTEM_PROMPT` is the other half - it asks the model for markup in
the site's own vocabulary so replies look like part of the app rather than
pasted-in text.
"""

from __future__ import annotations

import html as html_module
import re

import nh3

# Tags a reply may use.  Everything structural the site's styles cover, and
# nothing that can execute, embed or navigate on its own.
ALLOWED_TAGS = {
    "p", "br", "hr", "span", "div",
    "strong", "b", "em", "i", "u", "s", "del", "ins", "mark", "small", "sub", "sup",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "q", "cite",
    "code", "pre", "kbd", "samp", "var",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "a", "img", "figure", "figcaption",
    "details", "summary",
    "audio", "video", "source",
}

ALLOWED_ATTRIBUTES = {
    "*": {"class", "id", "title", "dir", "lang"},
    # `rel` is deliberately absent: nh3 sets it itself from `link_rel` below,
    # and allowing both is an error.
    "a": {"href", "target"},
    "img": {"src", "alt", "width", "height", "loading"},
    "audio": {"src", "controls", "preload"},
    "video": {"src", "controls", "poster", "preload", "playsinline", "width", "height"},
    "source": {"src", "type"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "ol": {"start"},
    "details": {"open"},
}

ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "data"}

# Wrapping every fragment in a known class keeps the site's typography applied
# no matter what the model returns.
FRAGMENT_CLASS = "ai-html"

CHAT_SYSTEM_PROMPT = """\
You are the assistant inside CRAFT, a generative studio. Your replies are \
inserted directly into the page's DOM, so you must answer with an HTML \
fragment and nothing else.

Rules for every reply:
- Output only HTML. No Markdown, no code fences around the whole answer, no \
<html>, <head>, <body>, <script>, <style>, <iframe> or event handler attributes.
- Use semantic tags: <p> for prose, <ul>/<ol> for lists, <h3>/<h4> for section \
headings, <table> for tabular data, <blockquote> for quotes.
- Show code with <pre><code class="language-NAME">...</code></pre> and escape \
&lt;, &gt; and &amp; inside it. Use <code> for inline code.
- The page is a dark glassmorphism interface with an indigo/violet accent. Match \
it with these helper classes when they fit, and no inline styles:
  - class="callout" on a <div> for a highlighted note
  - class="callout callout-warn" for a caution
  - class="pill" on a <span> for a short label or tag
  - class="muted" on any element for secondary text
- Keep it compact: no restating the question, no sign-offs, no wrapper <div> \
around the whole reply unless it is semantically meaningful.
- Write in the language the user used.
"""


def sanitize(fragment: str) -> str:
    """Strip anything unsafe from a model- or service-produced HTML fragment."""
    if not fragment:
        return ""
    return nh3.clean(
        fragment,
        tags=ALLOWED_TAGS,
        attributes={tag: set(attrs) for tag, attrs in ALLOWED_ATTRIBUTES.items()},
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )


def looks_like_html(text: str) -> bool:
    return bool(re.search(r"<(p|div|ul|ol|h[1-6]|pre|table|blockquote|span|code|img)\b", text, re.I))


def text_to_html(text: str) -> str:
    """Wrap plain text in paragraphs, preserving blank-line breaks."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]
    if not blocks:
        return ""
    return "".join(
        "<p>{}</p>".format(html_module.escape(block).replace("\n", "<br>")) for block in blocks
    )


def normalize_reply(text: str) -> str:
    """Turn whatever a model returned into a safe HTML fragment.

    Models mostly honour the system prompt, but not always: some wrap the whole
    answer in a ``` fence, and some ignore it and answer in plain text.
    """
    if not text:
        return ""

    stripped = text.strip()

    # Unwrap a single fence around the whole answer, e.g. ```html ... ```
    fence = re.match(r"^```(?:html)?\s*\n(?P<body>.*)\n```$", stripped, re.S | re.I)
    if fence:
        stripped = fence.group("body").strip()

    if not looks_like_html(stripped):
        stripped = text_to_html(stripped)

    return sanitize(stripped)


def fragment(inner_html: str, *, extra_class: str = "") -> str:
    """Wrap sanitised markup in the container the page styles."""
    classes = f"{FRAGMENT_CLASS} {extra_class}".strip()
    return f'<div class="{classes}">{inner_html}</div>'


def error_fragment(message: str) -> str:
    """A styled error block, safe to inject."""
    return fragment(
        f'<div class="callout callout-warn">{html_module.escape(message)}</div>',
        extra_class="ai-html-error",
    )


def html_to_text(fragment_html: str, *, limit: int = 3500) -> str:
    """Flatten an HTML fragment to plain text, for Telegram and previews."""
    if not fragment_html:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", fragment_html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|blockquote|pre)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]
