"""Parsing of the published KIE.ai documentation.

``docs.kie.ai`` publishes two things we rely on:

* ``llms.txt`` - a flat index listing every documentation page, grouped by the
  section it belongs to ("Image    Models > Google", "Suno API", ...).  It is a
  single small request, so it backs the model lists.
* one Markdown page per model, each embedding the model's OpenAPI
  specification.  That is where request schemas come from, so it is only
  fetched when a specific model is opened.

Nothing here touches Django, which keeps the parser usable from the catalog
refresh script as well.
"""

from __future__ import annotations

import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

DOCS_ROOT = "https://docs.kie.ai"
INDEX_URL = f"{DOCS_ROOT}/llms.txt"

INDEX_LINE_RE = re.compile(
    r"^-\s*(?P<section>[^\[]*)\[(?P<title>[^\]]*)\]"
    r"\((?P<url>https://docs\.kie\.ai/[^)]+\.md)\)\s*:?\s*(?P<summary>.*)$"
)
YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)\n```", re.S)
TAG_RE = re.compile(r"docs/en/(?P<section>[^/]+)(?:/(?P<group>[^/]+))?(?:/(?P<provider>.+))?")
CJK_RE = re.compile(r"[一-鿿]")
PERMALINK_RE = re.compile(r"^https://docs\.kie\.ai/\d+e0\.md$")

CATEGORIES = ("chat", "image", "video", "music")

# Section labels in llms.txt are inconsistently spaced ("Image    Models"), so
# categories are matched on substrings instead of exact names.
SECTION_KEYWORDS = (
    ("image", "image"),
    ("video", "video"),
    ("veo", "video"),
    ("runway", "video"),
    ("music", "music"),
    ("suno", "music"),
    ("audio", "music"),
    ("chat", "chat"),
)

# Model-id fragments that reveal the category when the doc tags do not.
CATEGORY_BY_FRAGMENT = (
    ("to-video", "video"),
    ("video", "video"),
    ("tts", "music"),
    ("text-to-speech", "music"),
    ("audio", "music"),
    ("music", "music"),
    ("image", "image"),
)

# Endpoints that exist alongside the model APIs but are not things a user
# generates with: uploads, download-link helpers and validation probes.
UTILITY_PATHS = (
    "/api/file-",
    "/api/v1/common/",
    "/api/v1/chat/credit",
)
UTILITY_SUFFIXES = ("/download-url", "/check-voice", "/validate", "/regenerate")

# The trailing segment of a creation path is a verb, not part of the product
# family, so it is dropped when deriving where to poll for results.
ACTION_SEGMENTS = {
    "generate", "create", "extend", "mashup", "sounds", "recovery",
    "upload-cover", "upload-extend", "add-instrumental", "add-vocals",
    "replace-section", "generate-persona", "cover",
}

# Families whose polling route does not follow the usual `/record-info`.
POLL_OVERRIDES = {
    "runway": "/api/v1/runway/record-detail",
}

# Title fragments marking a page as documentation rather than a model.
NON_MODEL_TITLES = (
    "callback",
    "quickstart",
    "details",
    "download url",
    "guide",
    "getting started",
    "record info",
    "check availability",
)

# Fields carrying the user's textual prompt, most specific first.
PROMPT_FIELDS = ("prompt", "text", "text_prompt", "query", "input_text", "lyrics")

# Fields accepting reference images.  Models are wildly inconsistent about
# naming these, so the list is deliberately long and order matters.
IMAGE_FIELDS = (
    "image_urls",
    "image_url",
    "input_urls",
    "images",
    "image",
    "input_image_urls",
    "input_image",
    "image_input",
    "reference_image_urls",
    "reference_image",
    "first_frame_url",
    "start_image_url",
    "source_image_url",
    "init_image_url",
    "filesUrl",
)


class DocsUnavailable(RuntimeError):
    """The documentation site could not be read."""


class NotMarkdown(DocsUnavailable):
    """The docs host answered with its HTML shell instead of the Markdown source."""


@dataclass(slots=True)
class ModelRef:
    """A model as listed in the documentation index, before its spec is read."""

    slug: str
    name: str
    category: str
    provider: str
    doc_url: str
    summary: str = ""

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "category": self.category,
            "provider": self.provider,
            "doc_url": self.doc_url,
            "summary": self.summary,
        }


@dataclass(slots=True)
class ModelSpec:
    """A model's full request contract, parsed from its OpenAPI specification."""

    slug: str
    model_id: str
    name: str
    provider: str
    category: str
    adapter: str
    create_path: str
    poll_path: str = ""
    description: str = ""
    doc_url: str = ""
    prompt_field: str = ""
    image_field: str = ""
    fields: list[dict] = field(default_factory=list)
    supports_streaming: bool = False
    # Whether the chat endpoint expects the model name in the body.  Several
    # OpenAI-compatible routes encode it in the path and reject it in the body.
    sends_model_in_body: bool = True

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "model_id": self.model_id,
            "name": self.name,
            "provider": self.provider,
            "category": self.category,
            "adapter": self.adapter,
            "create_path": self.create_path,
            "poll_path": self.poll_path,
            "description": self.description,
            "doc_url": self.doc_url,
            "prompt_field": self.prompt_field,
            "image_field": self.image_field,
            "fields": self.fields,
            "supports_streaming": self.supports_streaming,
            "sends_model_in_body": self.sends_model_in_body,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ModelSpec":
        known = {f for f in cls.__slots__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #


def fetch(url: str, attempts: int = 4, timeout: int = 30) -> str:
    """Download a documentation page as Markdown.

    The docs are served through an edge cache that occasionally answers a
    ``.md`` request with the single-page-app HTML instead, so a 200 is not on
    its own proof that we got the Markdown source.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "kie-web-client"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
            if body.lstrip().startswith("<"):
                raise NotMarkdown(url)
            return body
        except Exception as exc:  # noqa: BLE001 - every failure mode here is retryable
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.75 * (attempt + 1))
    raise DocsUnavailable(f"Could not read {url}: {last_error}") from last_error


def fetch_many(urls: list[str], workers: int = 6) -> list[tuple[str, str]]:
    """Download several pages concurrently, dropping the ones that fail."""

    def load(url: str) -> tuple[str, str]:
        try:
            return url, fetch(url)
        except DocsUnavailable:
            return url, ""

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(load, urls))


# --------------------------------------------------------------------------- #
# Index parsing
# --------------------------------------------------------------------------- #


def slug_for(doc_url: str) -> str:
    """Stable identifier for a model, derived from its documentation path."""
    path = doc_url.removeprefix(f"{DOCS_ROOT}/").removesuffix(".md")
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")


def provider_for_section(section: str) -> str:
    """Who publishes a model, read from its documentation section.

    Sections come in two shapes.  Model sections lead with the category -
    "Image    Models > Google", "Video Models > Runway API > Aleph" - and name
    the vendor next.  Product sections lead with the product itself -
    "Suno API > Music Generation" - where the rest is only how KIE grouped that
    product's operations.
    """
    parts = [part.strip() for part in re.split(r"[>/]", section) if part.strip()]
    parts = [part for part in parts if part not in {"Market", "Docs", "docs", "en"}]
    if not parts:
        return "KIE"

    if parts[0].lower().endswith("models"):
        parts = parts[1:]
    if not parts:
        return "KIE"

    return re.sub(r"\s*API$", "", parts[0], flags=re.I).strip() or "KIE"


def category_for_section(section: str) -> str:
    lowered = section.lower()
    for keyword, category in SECTION_KEYWORDS:
        if keyword in lowered:
            return category
    return ""


def infer_category(model_id: str, doc_url: str, fallback: str = "image") -> str:
    haystack = f"{model_id} {doc_url}".lower()
    for fragment, category in CATEGORY_BY_FRAGMENT:
        if fragment in haystack:
            return category
    return fallback


def parse_index(index: str) -> list[ModelRef]:
    """Turn ``llms.txt`` into one :class:`ModelRef` per documented model."""
    refs: dict[str, ModelRef] = {}
    for line in index.splitlines():
        match = INDEX_LINE_RE.match(line.strip())
        if not match:
            continue

        url = match.group("url")
        if url.startswith(f"{DOCS_ROOT}/cn") or PERMALINK_RE.match(url):
            # Localized mirrors and numeric permalinks duplicate market pages.
            continue

        title = match.group("title").strip()
        section = match.group("section").strip()
        if CJK_RE.search(title) or CJK_RE.search(section):
            continue

        category = category_for_section(section)
        if not category:
            continue

        # A section also holds pages that are not models: callbacks, polling
        # routes, and prose guides.
        lowered = title.lower()
        if any(word in lowered for word in NON_MODEL_TITLES):
            continue

        ref = ModelRef(
            slug=slug_for(url),
            name=title,
            category=category,
            provider=provider_for_section(section),
            doc_url=url,
            summary=match.group("summary").strip(),
        )
        refs.setdefault(ref.slug, ref)

    return sorted(refs.values(), key=lambda r: (r.provider.lower(), r.name.lower()))


def fetch_index() -> list[ModelRef]:
    return parse_index(fetch(INDEX_URL))


# --------------------------------------------------------------------------- #
# Specification parsing
# --------------------------------------------------------------------------- #


def _load_yaml(markdown: str) -> dict | None:
    import yaml  # imported lazily: only spec parsing needs it

    block = YAML_BLOCK_RE.search(markdown)
    if not block:
        return None
    try:
        return yaml.safe_load(block.group(1))
    except yaml.YAMLError:
        return None


# Every market page ends with the same integration boilerplate. It is aimed at
# API consumers, not at someone picking a model in a UI, so it is cut away.
BOILERPLATE_MARKERS = (
    "After submitting a task",
    "Query Task Status",
    "For production use",
    "Related Resources",
    "Market Overview",
    "Learn how to query task status",
)


def clean_text(text: str | None, limit: int = 600) -> str:
    """Turn a documentation description into a sentence or two of plain prose."""
    if not text:
        return ""

    text = re.sub(r"<Card[^>]*>.*?</Card>", "", text, flags=re.S)
    text = re.sub(r"</?(CardGroup|Card|Steps|Step|Tabs|TabItem)[^>]*>", "", text)
    # Callout syntax appears as ":::tip[Title]" and ":::" - and, once the
    # markers are gone, would otherwise leave a stray "tip[]" behind.
    text = re.sub(r":::\s*[a-z]*\s*\[[^\]]*\]", "", text, flags=re.I)
    text = re.sub(r":::\s*[a-z]*", "", text)
    text = re.sub(r"^\s*#+.*$", "", text, flags=re.M)

    for marker in BOILERPLATE_MARKERS:
        position = text.find(marker)
        if position > 0:
            text = text[:position]

    text = re.sub(r"\n{2,}", "\n", text).strip()
    text = text.rstrip(" \n:-")
    return text[:limit].strip()


def classify_tags(tags: list[str]) -> tuple[str, str]:
    """Return ``(category, provider)`` for an OpenAPI operation's tags.

    Tags mirror the documentation's own section path, e.g.
    ``docs/en/Market/Image    Models/Google``.
    """
    for tag in tags or []:
        match = TAG_RE.match(tag)
        if not match:
            continue
        section = " > ".join(
            part for part in (match.group("section"), match.group("group"), match.group("provider"))
            if part
        )
        return category_for_section(section), provider_for_section(section)
    return "", ""


def first_present(candidates: tuple[str, ...], properties: dict) -> str:
    for name in candidates:
        if name in properties:
            return name
    return ""


def simplify_property(name: str, schema: dict) -> dict:
    """Reduce an OpenAPI property to what the form renderer needs."""
    kind = schema.get("type") or ("array" if "items" in schema else "string")
    examples = schema.get("examples") or []
    example = examples[0] if examples else None
    if isinstance(example, (dict, list)):
        example = None

    simplified = {
        "name": name,
        "type": kind,
        "label": name.replace("_", " ").strip().title(),
        "description": clean_text(schema.get("description"), 400),
        "enum": [v for v in (schema.get("enum") or []) if isinstance(v, (str, int, float))],
        "default": schema.get("default"),
        "minimum": schema.get("minimum"),
        "maximum": schema.get("maximum"),
        "min_length": schema.get("minLength"),
        "max_length": schema.get("maxLength"),
        "max_items": schema.get("maxItems"),
        "format": schema.get("format", ""),
        "example": example,
        "required": False,
    }
    if kind == "array":
        items = schema.get("items") or {}
        simplified["item_type"] = items.get("type", "string")
        simplified["item_format"] = items.get("format", "")
    return simplified


def request_example(operation: dict) -> dict:
    try:
        media = operation["requestBody"]["content"]["application/json"]
    except (KeyError, TypeError):
        return {}
    example = media.get("example")
    if isinstance(example, dict):
        return example
    examples = media.get("examples")
    if isinstance(examples, list) and examples and isinstance(examples[0], dict):
        return examples[0]
    if isinstance(examples, dict):
        for value in examples.values():
            if isinstance(value, dict) and isinstance(value.get("value"), dict):
                return value["value"]
    return {}


def _parse_jobs_model(operation: dict, schema: dict, ref: ModelRef) -> ModelSpec | None:
    properties = schema.get("properties") or {}
    model_property = properties.get("model") or {}
    enum = model_property.get("enum") or []
    model_id = enum[0] if enum else model_property.get("default")
    if not model_id:
        return None

    input_schema = properties.get("input") or {}
    input_properties = input_schema.get("properties") or {}
    required = input_schema.get("required") or []

    fields = []
    for name, sub in input_properties.items():
        simplified = simplify_property(name, sub or {})
        simplified["required"] = name in required
        fields.append(simplified)

    category, provider = classify_tags(operation.get("tags") or [])
    return ModelSpec(
        slug=ref.slug,
        model_id=model_id,
        name=ref.name or operation.get("summary") or model_id,
        provider=provider or ref.provider or model_id.split("/")[0].title(),
        category=category or ref.category or infer_category(model_id, ref.doc_url),
        adapter="jobs",
        create_path="/api/v1/jobs/createTask",
        poll_path="/api/v1/jobs/recordInfo",
        description=clean_text(operation.get("description")),
        doc_url=ref.doc_url,
        prompt_field=first_present(PROMPT_FIELDS, input_properties),
        image_field=first_present(IMAGE_FIELDS, input_properties),
        fields=fields,
    )


def _chat_model_ids(path: str, operation: dict, model_property: dict) -> list[str]:
    """Recover the model identifiers a chat endpoint accepts.

    Some pages declare an ``enum`` (occasionally several models behind one
    endpoint), others only show the value in an example request or name it in
    the property description.
    """
    ids = [v for v in (model_property.get("enum") or []) if isinstance(v, str)]
    if ids:
        return ids

    default = model_property.get("default")
    if isinstance(default, str) and default:
        return [default]

    example_model = request_example(operation).get("model")
    if isinstance(example_model, str) and example_model:
        return [example_model]

    quoted = re.findall(r"`([a-z0-9][a-z0-9._-]{2,})`", model_property.get("description") or "")
    if quoted:
        return quoted

    if "/models/" in path:
        return [path.split("/models/")[-1].split(":")[0]]
    return [path.strip("/").split("/")[0]]


def _chat_protocol(path: str) -> str:
    if path.endswith("/chat/completions"):
        return "openai"
    if path.endswith("/v1/messages"):
        return "anthropic"
    if path.endswith("/v1/responses"):
        return "responses"
    if ":streamGenerateContent" in path or ":generateContent" in path:
        return "gemini"
    return ""


def _parse_chat_models(path: str, operation: dict, schema: dict, ref: ModelRef) -> list[ModelSpec]:
    protocol = _chat_protocol(path)
    if not protocol:
        return []

    properties = schema.get("properties") or {}
    _, provider = classify_tags(operation.get("tags") or [])
    model_ids = _chat_model_ids(path, operation, properties.get("model") or {})

    specs = []
    for index, model_id in enumerate(model_ids):
        # A page can document several models behind one endpoint; only the
        # first keeps the page's human-facing title.
        specs.append(
            ModelSpec(
                slug=ref.slug if index == 0 else f"{ref.slug}-{re.sub(r'[^a-z0-9]+', '-', model_id.lower())}",
                model_id=model_id,
                name=ref.name if index == 0 else model_id,
                provider=provider or ref.provider,
                category="chat",
                adapter=f"chat_{protocol}",
                create_path=path,
                description=clean_text(operation.get("description")),
                doc_url=ref.doc_url,
                prompt_field="messages",
                image_field="image_url" if protocol in {"openai", "anthropic", "gemini"} else "",
                supports_streaming="stream" in properties,
                sends_model_in_body="model" in properties,
            )
        )
    return specs


def is_utility(path: str) -> bool:
    """Whether a POST endpoint is plumbing rather than a model."""
    return path.startswith(UTILITY_PATHS) or path.endswith(UTILITY_SUFFIXES)


def poll_path_for(create_path: str) -> str:
    """Where to ask about a task started at ``create_path``.

    KIE's product APIs all follow ``/api/v1/<family>/record-info``; the family
    is the creation path with its action verb removed.
    """
    if not create_path.startswith("/api/v1/"):
        return ""

    segments = [segment for segment in create_path.removeprefix("/api/v1/").split("/") if segment]
    if len(segments) > 1 and segments[-1] in ACTION_SEGMENTS:
        segments = segments[:-1]
    if not segments:
        return ""

    family = "/".join(segments)
    return POLL_OVERRIDES.get(family, f"/api/v1/{family}/record-info")


def _parse_task_model(path: str, operation: dict, schema: dict, ref: ModelRef) -> ModelSpec:
    """A model on one of KIE's dedicated product endpoints (Suno, Veo, Runway...).

    These predate the unified jobs endpoint but share its shape: POST a body,
    get a task id, poll for the result.  Everything about them is read from the
    documentation, so a new one needs no code here.
    """
    properties = schema.get("properties") or {}
    required = schema.get("required") or []

    fields = []
    for name, sub in properties.items():
        if name == "callBackUrl":
            continue
        simplified = simplify_property(name, sub or {})
        simplified["required"] = name in required
        fields.append(simplified)

    category, provider = classify_tags(operation.get("tags") or [])
    return ModelSpec(
        slug=ref.slug,
        # These endpoints take no model name; the path is the identity.
        model_id=path.removeprefix("/api/v1/"),
        name=ref.name or operation.get("summary") or path,
        provider=provider or ref.provider,
        category=category or ref.category or infer_category(path, ref.doc_url),
        adapter="task",
        create_path=path,
        poll_path=poll_path_for(path),
        description=clean_text(operation.get("description")),
        doc_url=ref.doc_url,
        prompt_field=first_present(PROMPT_FIELDS, properties),
        image_field=first_present(IMAGE_FIELDS, properties),
        fields=fields,
    )


def parse_specs(markdown: str, ref: ModelRef) -> list[ModelSpec]:
    """Extract every model documented on one page."""
    document = _load_yaml(markdown)
    if not document:
        return []

    specs: list[ModelSpec] = []
    for path, operations in (document.get("paths") or {}).items():
        if not isinstance(operations, dict):
            continue
        for method, operation in operations.items():
            if method.lower() != "post" or not isinstance(operation, dict):
                continue
            try:
                schema = operation["requestBody"]["content"]["application/json"]["schema"]
            except (KeyError, TypeError):
                continue
            if not isinstance(schema, dict):
                continue

            if path == "/api/v1/jobs/createTask":
                spec = _parse_jobs_model(operation, schema, ref)
                if spec:
                    specs.append(spec)
            elif _chat_protocol(path):
                specs.extend(_parse_chat_models(path, operation, schema, ref))
            elif not is_utility(path):
                specs.append(_parse_task_model(path, operation, schema, ref))
    return specs


def fetch_specs(ref: ModelRef) -> list[ModelSpec]:
    return parse_specs(fetch(ref.doc_url), ref)
