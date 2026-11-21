import json
import os
import re
import time
from enum import IntEnum
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


class CompanyStatus(IntEnum):
    OK = 0
    NO_IMPRESSUM_LINK_FOUND = 1
    PARSING_FAILED = 2
    ADDRESS_INCOMPLETE = 3
    ERROR = 4
    NO_WEBSITE = 5


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
MAX_TEXT_CHARS = 8000

SYSTEM_PROMPT = """\
You extract legally required company information ("Impressum" / "Imprint") from
German-language website text.

Return ONLY a valid JSON object (no markdown fences, no commentary) that parses
with json.loads. The JSON must have exactly these keys:

{
  "company_name": "<string or null>",
  "postal_address": {
    "street": "<string or null>",
    "house_no": "<string or null>",
    "postcode": "<string or null>",
    "city": "<string or null>",
    "country": "<string or null>"
  },
  "handelsregister": "<string or null>",
  "vat_number": "<string or null>",
  "phone": "<string or null>",
  "email": "<string or null>",
  "responsible_person": "<string or null>"
}

Rules:
- Extract ONLY information actually present in the text; use null when absent.
- Do not invent or guess values.
- phone: keep the international format when possible (e.g. +49 ...).
- vat_number: German VAT ids look like DE followed by 9 digits.
- email: lowercase.
- responsible_person: the named person or role legally responsible for the
  content (e.g. Geschaeftsfuehrer, Vorstand, "inhaltlich verantwortlich").
- Output valid JSON only.
"""

BOILERPLATE_EXACT_RE = re.compile(
    r"^(?:"
    r"datenschutz(?:erkl[äa]rung)?|agb|allgemeine gesch[äa]ftsbedingungen|"
    r"cookies?|cookie[- ]?(?:einstellungen|hinweis|richtlinie|policy)|"
    r"men[üu]|menu|home|startseite|kontakt|suche|search|"
    r"deutsch|english|fran[çc]ais|italiano|espa[ñn]ol|"
    r"nach oben|top|schlie[ßs]en|close|akzeptieren|ablehnen|accept|decline|"
    r"alle akzeptieren|nur notwendige|warenkorb|anmelden|login|newsletter"
    r")$",
    re.IGNORECASE,
)

BOILERPLATE_SUBSTR_RE = re.compile(
    r"(?:"
    r"cookie[- ]?(?:banner|consent|einstellungen|akzeptieren|zustimmung)|"
    r"wir verwenden cookies|diese website nutzt cookies|"
    r"alle rechte vorbehalten|all rights reserved|"
    r"©\s*(?:19|20)\d{2}"
    r")",
    re.IGNORECASE,
)

HEAD_ANCHOR_RE = re.compile(
    r"\b(?:"
    r"angaben\s+gem[äa][ßs]\s*§\s*5\s*(?:tmg|ddg)|"
    r"angaben\s+gem\.\s*§\s*5|"
    r"impressum|imprint|anbieterkennzeichnung|diensteanbieter|anbieter"
    r")\b",
    re.IGNORECASE,
)

TAIL_PATTERNS = [
    re.compile(r"(?:\+49|0049)[\s\-\./()]*\d[\d\s\-\./()]{4,}"),
    re.compile(r"\bDE\s?\d{9}\b", re.IGNORECASE),
    re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    re.compile(r"\b(?:HRB|HRA)\s?\d+\b|\b(?:Registergericht|Amtsgericht)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:Verantwortlich|Vertreten\s+durch|Gesch[äa]ftsf[üu]hrer(?:in)?|"
        r"Vorstand|Inhaltlich\s+verantwortlich|Inhaber(?:in)?)\b",
        re.IGNORECASE,
    ),
]

LEGAL_LINE_KEY_RE = re.compile(
    r"(?:"
    r"impressum|imprint|§\s*5|tmg|ddg|gmbh|gbr|ag\b|ug\b|kg\b|ohg\b|e\.?k\.?|"
    r"stra[ßs]e|str\.|weg\b|platz\b|allee\b|tel|telefon|fax|mail|e-mail|"
    r"ust|vat|steuer|hrb|hra|register|amtsgericht|vertreten|gesch[äa]ftsf[üu]hrer|"
    r"vorstand|inhaber|verantwortlich|\+49|0049|DE\s?\d{9}|@|\b\d{5}\b"
    r")",
    re.IGNORECASE,
)


class DeepSeekTransportError(RuntimeError):
    """Raised when network or HTTP error happens talking to DeepSeek API."""


def estimate_tokens(text: str) -> int:
    return len(text) // 4


def normalize_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    cleaned = raw_url.strip()
    if not cleaned:
        return None
    if not re.match(r"^https?://", cleaned, re.IGNORECASE):
        cleaned = f"https://{cleaned}"
    return cleaned


def fetch_html(url: str, timeout: int = 20) -> Tuple[str, str]:
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text, resp.url


def find_impressum_link(html: str, base_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.find_all("a", href=True)

    def _is_navigable(href: str) -> bool:
        low = href.strip().lower()
        return bool(low) and not low.startswith(("javascript:", "mailto:", "tel:", "#"))

    for a in anchors:
        href = a.get("href", "").strip()
        if not _is_navigable(href):
            continue
        vis_text = " ".join(a.get_text(" ", strip=True).split()).lower()
        if vis_text in ("impressum", "imprint"):
            return urljoin(base_url, href)

    for a in anchors:
        href = a.get("href", "").strip()
        if not _is_navigable(href):
            continue
        low_href = href.lower()
        if "impressum" in low_href or "imprint" in low_href:
            return urljoin(base_url, href)

    for a in anchors:
        href = a.get("href", "").strip()
        if not _is_navigable(href):
            continue
        vis_text = " ".join(a.get_text(" ", strip=True).split()).lower()
        if "impressum" in vis_text or "imprint" in vis_text:
            return urljoin(base_url, href)

    return None


def _decode_cf_email(encoded_hex: str) -> Optional[str]:
    try:
        key = int(encoded_hex[:2], 16)
        chars = [
            chr(int(encoded_hex[i : i + 2], 16) ^ key)
            for i in range(2, len(encoded_hex), 2)
        ]
        return "".join(chars)
    except (ValueError, IndexError):
        return None

def extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    for cf_tag in soup.select("[data-cfemail]"):
        decoded = _decode_cf_email(cf_tag.get("data-cfemail", ""))
        if decoded:
            cf_tag.string = decoded
    for tag in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "form"]):
        tag.decompose()

    raw_text = soup.get_text("\n")
    lines = []
    for raw_line in raw_text.splitlines():
        normalized = " ".join(raw_line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def _strip_boilerplate_lines(text: str) -> str:
    kept: List[str] = []
    seen_impressum_nav = False
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if BOILERPLATE_EXACT_RE.match(stripped):
            continue
        if len(stripped) < 180 and BOILERPLATE_SUBSTR_RE.search(stripped):
            continue
        if stripped.lower() in ("impressum", "imprint") and idx < 15 and not seen_impressum_nav:
            has_later_anchor = any(HEAD_ANCHOR_RE.search(l) for l in lines[idx + 1 :])
            if has_later_anchor:
                seen_impressum_nav = True
                continue
        kept.append(stripped)
    return "\n".join(kept)


def _has_strong_signal(text: str) -> bool:
    return any(p.search(text) for p in TAIL_PATTERNS)


def _keep_key_lines_with_context(text: str, window: int = 2) -> str:
    lines = text.splitlines()
    keep_idx = set()
    for idx, line in enumerate(lines):
        if LEGAL_LINE_KEY_RE.search(line):
            for j in range(max(0, idx - window), min(len(lines), idx + window + 1)):
                keep_idx.add(j)
    if not keep_idx:
        return text
    return "\n".join(lines[i] for i in sorted(keep_idx))


def _head_tail_window(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    sep = "\n...\n"
    half = max(1, (max_chars - len(sep)) // 2)
    return f"{text[:half]}{sep}{text[-half:]}"


def optimize_impressum_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> Tuple[str, Dict[str, object]]:
    before_chars = len(text)
    before_tokens = estimate_tokens(text)

    if before_chars <= max_chars:
        stats = {
            "before_chars": before_chars,
            "after_chars": before_chars,
            "before_tokens": before_tokens,
            "after_tokens": before_tokens,
            "strategy": "unchanged_under_threshold",
        }
        return text, stats

    cleaned = _strip_boilerplate_lines(text)
    if len(cleaned) <= max_chars:
        stats = {
            "before_chars": before_chars,
            "after_chars": len(cleaned),
            "before_tokens": before_tokens,
            "after_tokens": estimate_tokens(cleaned),
            "strategy": "boilerplate_strip",
        }
        return cleaned, stats

    head_match = HEAD_ANCHOR_RE.search(cleaned)
    head_start = max(0, head_match.start() - 120) if head_match else 0

    last_tail_end = None
    for pattern in TAIL_PATTERNS:
        for match in pattern.finditer(cleaned):
            if last_tail_end is None or match.end() > last_tail_end:
                last_tail_end = match.end()

    tail_margin = 400
    if last_tail_end is not None:
        tail_end = min(len(cleaned), last_tail_end + tail_margin)
    else:
        tail_end = len(cleaned)

    if tail_end <= head_start:
        candidate = cleaned
        strategy = "fallback_cleaned"
    else:
        candidate = cleaned[head_start:tail_end]
        strategy = "sandwich_head_tail"

    if not (HEAD_ANCHOR_RE.search(candidate) and _has_strong_signal(candidate)):
        candidate = cleaned
        strategy = "relaxed_verification_failed"

    if len(candidate) > max_chars:
        compacted = _keep_key_lines_with_context(candidate, window=2)
        if len(compacted) <= max_chars and _has_strong_signal(compacted):
            candidate = compacted
            strategy = f"{strategy}+key_lines"
        else:
            candidate = _head_tail_window(candidate, max_chars)
            strategy = f"{strategy}+head_tail_window"

    stats = {
        "before_chars": before_chars,
        "after_chars": len(candidate),
        "before_tokens": before_tokens,
        "after_tokens": estimate_tokens(candidate),
        "strategy": strategy,
    }
    return candidate, stats


def call_deepseek(
    optimized_text: str,
    api_key: str,
    model: Optional[str] = None,
    max_retries: int = 3,
    timeout: int = 60,
) -> Optional[Dict]:
    if not api_key:
        raise DeepSeekTransportError("DEEPSEEK_API_KEY is not set")

    chosen_model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Extract the Impressum data from the text below and return JSON:\n\n{optimized_text}",
            },
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 1024,
        "temperature": 0,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(DEEPSEEK_ENDPOINT, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            raise DeepSeekTransportError(f"DeepSeek request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise DeepSeekTransportError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DeepSeekTransportError(f"Unexpected DeepSeek response structure: {exc}") from exc

        if content and content.strip():
            cleaned_content = content.strip()
            if cleaned_content.startswith("```"):
                cleaned_content = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned_content, flags=re.IGNORECASE)
            try:
                parsed = json.loads(cleaned_content)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        if attempt < max_retries - 1:
            time.sleep(2**attempt)

    return None


def _clean_str(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() == "null":
        return None
    return s


def evaluate_extraction(parsed_json: Optional[Dict], fallback_company_name: str) -> Tuple[CompanyStatus, Dict]:
    if not parsed_json or not isinstance(parsed_json, dict):
        return CompanyStatus.PARSING_FAILED, {
            "legal_name": fallback_company_name,
            "postal_address": None,
            "handelsregister": None,
            "vat_number": None,
            "phone": None,
            "email": None,
            "responsible_person": None,
            "raw_json": parsed_json,
        }

    raw_addr = parsed_json.get("postal_address")
    addr = raw_addr if isinstance(raw_addr, dict) else {}

    street = _clean_str(addr.get("street"))
    house_no = _clean_str(addr.get("house_no"))
    postcode = _clean_str(addr.get("postcode"))
    city = _clean_str(addr.get("city"))
    country = _clean_str(addr.get("country"))

    extracted_company = _clean_str(parsed_json.get("company_name"))
    handelsregister = _clean_str(parsed_json.get("handelsregister"))
    vat_number = _clean_str(parsed_json.get("vat_number"))
    phone = _clean_str(parsed_json.get("phone"))
    email = _clean_str(parsed_json.get("email"))
    if email:
        email = email.lower()
    responsible_person = _clean_str(parsed_json.get("responsible_person"))

    any_detected = any([
        extracted_company,
        street,
        house_no,
        postcode,
        city,
        country,
        handelsregister,
        vat_number,
        phone,
        email,
        responsible_person,
    ])

    line_street = " ".join(p for p in (street, house_no) if p)
    line_city = " ".join(p for p in (postcode, city) if p)
    postal_address_str = ", ".join(p for p in (line_street, line_city, country) if p) or None

    mapped = {
        "legal_name": extracted_company or fallback_company_name,
        "postal_address": postal_address_str,
        "handelsregister": handelsregister,
        "vat_number": vat_number,
        "phone": phone,
        "email": email,
        "responsible_person": responsible_person,
        "raw_json": parsed_json,
    }

    if not any_detected:
        return CompanyStatus.PARSING_FAILED, mapped

    address_complete = all([postcode, city, street, house_no])
    if not address_complete:
        return CompanyStatus.ADDRESS_INCOMPLETE, mapped

    return CompanyStatus.OK, mapped