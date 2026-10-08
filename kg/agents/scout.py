"""Scout — finds many sources for one live-data need and writes a small parser for each one that can be read.

Three focused calls (core/scout.py runs the probing, testing and cross-checking between them):
    plan        the need → the group's shape: name, parameters, output fields (which are numbers, which are key)
    candidates  the need + web search hits → 20-30 candidate sources: documented APIs and web pages
    parser      one candidate + what it actually returned → a sensor module for it (fixed once with the test error)
"""

import asyncio
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior

from core import sensors
from core.llm import model_for, model_settings

MODEL = model_for("scout")


class _Unwrap(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def unwrap(cls, v):
        if isinstance(v, dict) and "arguments" in v:
            return v["arguments"]
        return v


class Param(BaseModel):
    name: str
    type: Literal["string", "number", "integer", "boolean"] = "string"
    description: str = ""
    example: str | float | int | bool = ""
    other_example: str | float | int | bool | None = Field(
        default=None, description="A second, different realistic value (e.g. AMD for NVDA) — parsers are tested on both")


class OutField(BaseModel):
    name: str = Field(description="Top-level key of the output dict, snake_case")
    description: str = ""
    numeric: bool = Field(default=False, description="The value is a number")
    key: bool = Field(default=False, description="The value this need is about")
    comparable: bool = Field(default=False, description="Every honest source shows the same value at the same time "
                                                         "(a price, a rate) — false for counts, lists, text")


class Plan(_Unwrap):
    name: str = Field(description="snake_case name for the sensor, e.g. stock_quote")
    description: str = Field(description="What it returns, one sentence")
    params: list[Param] = Field(default_factory=list)
    fields: list[OutField] = Field(default_factory=list)

    @field_validator("params", "fields", mode="before")
    @classmethod
    def _null_list(cls, v):
        return [] if v is None else v


class Candidate(BaseModel):
    kind: Literal["api", "page"]
    url: str = Field(description="URL template; placeholders like {symbol} are the parameters")
    why: str = ""


class Candidates(_Unwrap):
    candidates: list[Candidate] = Field(default_factory=list)

    @field_validator("candidates", mode="before")
    @classmethod
    def _null_list(cls, v):
        return [] if v is None else v


class Parser(_Unwrap):
    code: str | None = Field(default=None, description="The module — null if this source doesn't have the data")
    note: str = ""


PLAN = """You design a live-data SENSOR for a need: its name, parameters and output fields. Many different sources
(APIs and web pages) will each implement exactly this shape, so keep it to what most sources show.
- params: what IDENTIFIES the thing measured (a ticker, a city, a currency pair, a country) — never how to show it
  (not a unit, a format or a language: sites don't put those in their addresses). Each with a realistic example value
  and a different other_example (a parser that only works for one value is useless).
- fields: the top-level output keys. Mark numbers numeric=true. Mark key=true on the ONE value the need is about
  (a price, a rate, a temperature; for headlines, the list of items — never a count). Every other field is optional:
  sources differ in what else they show, and a source must not be refused for lacking an extra.
- Units: don't put a unit in a field's name (not temperature_celsius) — sources show different units; add a separate
  text field for the unit (e.g. temperature + temperature_unit) and let each source report what it shows.
  comparable=true only where every honest source shows the same value at the same time (a price, an exchange rate);
  false for counts, lists and text, which differ by source. Don't invent fields only one source would have.
If an EXISTING SHAPE is given, keep its name, parameters and field names exactly; only mark the key fields and give
each parameter an other_example."""

CANDIDATES = """You list candidate SOURCES for a live-data need: as many different sites as you can (20-30), each one a
place that shows this data for the given parameters.
- kind "api": a documented or well-known public JSON / CSV / RSS endpoint that needs no key.
- kind "page": a public web page that shows the value (quote pages, official status pages, news sites, statistics
  offices, exchanges) — it will be opened in a real browser.
- url: the exact URL template with the parameters as {placeholders} (e.g. https://www.cnbc.com/quotes/{symbol}).
- One candidate per site; include sites from the SEARCH HITS. The most famous sites often refuse automated readers,
  so go wide: official sources, exchanges, brokers, data aggregators, smaller and regional sites, sites in other
  languages.
- Never repeat a site listed under ALREADY TRIED.
Never include login-only or paid pages.

Answer with one candidate per line and nothing else:
page https://www.example.com/quote/{symbol}
api https://api.example.org/v1/price?ticker={symbol}"""

PARSER = r"""You write ONE sensor module for one SOURCE, from what that source actually returned (SAMPLE).

{contract}

For this job:
- NAME = the sensor name given; DESCRIPTION as given; PARAMS exactly as given (same names, types, examples).
- Return a dict with the FIELDS given (same keys; numbers as numbers, not strings) plus "source" (the URL read).
  A required field missing → raise. An (optional) field the source doesn't show → leave it out; never raise for it.
- Use only what the SAMPLE shows: its labels, its keys, its format.

kind "page" — the page is ALREADY opened in a browser for you: page("main") returns exactly the SAMPLE's kind of
text. Never fetch it yourself and don't import httpx. Anchor each regex on the label or text right next to the value
as it appears in the SAMPLE — a page shows many numbers (the day's high, chart axes, other tickers, articles):

    import re
    NAME = "..."
    DESCRIPTION = "..."
    PARAMS = {{...}}
    PAGES = {{"main": "<the URL template, with the {{placeholders}}>"}}

    def run(symbol: str) -> dict:
        text = page("main")
        m = re.search(r"<label as in the SAMPLE>\s*\$?([\d,]+\.\d+)", text)
        if not m:
            raise ValueError("price not found on the page")
        return {{"price": float(m.group(1).replace(",", "")), "source": "<the URL>"}}

kind "api": fetch the URL template with httpx (filled with the parameters) and read the fields from the response.

Answer with the complete module in ONE ```python block and nothing else. If the SAMPLE doesn't contain the key values
for the example parameters, answer only: NO DATA: <why>"""

_plan = Agent(MODEL, name="scout_plan", model_settings=model_settings(decisive=True), system_prompt=PLAN,
              output_type=Plan, retries=3)
# Plain lines out, like the parser: a long list as JSON broke the local model's output once the tried-list grew.
_candidates = Agent(MODEL, name="scout_candidates", model_settings=model_settings(), system_prompt=CANDIDATES,
                    output_type=str, retries=2)
_LINE = re.compile(r"^[\s\-*\d.)]*(api|page)\W+(https?://\S+)", re.I | re.M)
# Plain text out: a local model wrapping a whole module in a JSON tool call broke it often (invalid JSON, code in the
# wrong place); a fenced block is what it writes reliably.
_parser = Agent(MODEL, name="scout_parser", model_settings=model_settings(decisive=True),
                system_prompt=PARSER.format(contract=sensors.CONTRACT), output_type=str, retries=2)
_CODE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)


async def _again(agent: Agent, prompt: str, attempts: int = 3):
    """A local model sometimes writes a broken tool call (Ollama answers 500: "XML syntax error"): ask again."""
    for attempt in range(attempts):
        try:
            return (await agent.run(prompt)).output
        except (ModelHTTPError, UnexpectedModelBehavior):
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(2)


async def plan(need: str, domain: str, existing: str = "") -> Plan:
    prompt = f"DOMAIN: {domain}\nNEED: {need}" + (f"\n\nEXISTING SHAPE:\n{existing}" if existing else "")
    return await _again(_plan, prompt)


async def candidates(need: str, shape: str, hits: str, tried: list[str] = ()) -> list[Candidate]:
    prompt = f"NEED: {need}\n\nSENSOR SHAPE:\n{shape}\n\nSEARCH HITS:\n{hits or '(none)'}"
    if tried:
        prompt += f"\n\nALREADY TRIED (list 20 OTHER sites): {', '.join(tried)}"
    text = await _again(_candidates, prompt)
    return [Candidate(kind=kind.lower(), url=url.rstrip(".,;)`")) for kind, url in _LINE.findall(text)]


async def parser(shape: str, kind: str, url: str, sample: str, problem: str = "", code: str = "",
                 hint: str = "") -> Parser:
    prompt = f"SENSOR:\n{shape}\n\nSOURCE: kind={kind} url={url}\n\nSAMPLE (for the example parameters):\n{sample}"
    if hint:
        prompt += f"\n\nTHE VALUES ARE IN THIS TEXT ON THE PAGE (anchor on it): {hint}"
    if problem:
        prompt += f"\n\nYOUR MODULE:\n```python\n{code}\n```\n\nIT FAILED THE TEST: {problem}\nFix it (or answer NO DATA)."
    text = (await _again(_parser, prompt)).strip()
    if m := _CODE.search(text):
        return Parser(code=m.group(1).strip())
    if "def run" in text and "NAME" in text:
        return Parser(code=text)
    return Parser(note=text.removeprefix("NO DATA:").strip()[:300] or "no module")


# ── Extraction: reading a page directly, every value backed by the text it came from ────────────────────────────────

EXTRACT = r"""You read the current values of a SENSOR's FIELDS for the given PARAMETERS from one web PAGE (its text).
- Copy every value exactly as the page shows it — no computing, no unit conversion, no rounding.
- For every value give EVIDENCE: the exact span of page text (at most 120 characters) that contains it, copied
  character for character.
- Only values for exactly these PARAMETERS (not another ticker, city or currency pair) and only current ones (not
  yesterday's close, a forecast or a chart axis).
- If the page doesn't show the KEY value for these parameters, answer found = false.
Answer with one ```json block and nothing else:
{"found": true, "values": {"<field>": <value>, ...}, "evidence": {"<field>": "<exact text>", ...}}"""

_extract = Agent(MODEL, name="scout_extract", model_settings=model_settings(decisive=True), system_prompt=EXTRACT,
                 output_type=str, retries=2)
_JSON = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


class Extracted(BaseModel):
    found: bool = False
    values: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)

    @field_validator("values", "evidence", mode="before")
    @classmethod
    def _null_dict(cls, v):
        return {} if v is None else v


async def extract(shape: str, params: dict, text: str) -> Extracted:
    """The model reads the page; never raises (not found on failure)."""
    prompt = f"SENSOR:\n{shape}\n\nPARAMETERS: {json.dumps(params)}\n\nPAGE:\n{text}"
    try:
        answer = await _again(_extract, prompt)
        m = _JSON.search(answer)
        return Extracted.model_validate_json(m.group(1) if m else answer.strip())
    except Exception:
        return Extracted()
