import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import Json, RealDictCursor
import requests

from impressum import (
    CompanyStatus,
    DeepSeekTransportError,
    call_deepseek,
    evaluate_extraction,
    extract_visible_text,
    fetch_html,
    find_impressum_link,
    normalize_url,
    optimize_impressum_text,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("enrich_companies")


def load_dotenv_stdlib(dotenv_path: Path = Path(".env")) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_connection():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "videos"),
        user=os.environ.get("DB_USER", "video_user"),
        password=os.environ.get("DB_PASSWORD", "video_pass"),
    )


def ensure_schema() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS companies (
        id                 SERIAL PRIMARY KEY,
        company_name       TEXT NOT NULL UNIQUE,
        language           TEXT,
        country            TEXT,
        postcode           TEXT,
        city               TEXT,
        street             TEXT,
        house_no           TEXT,
        web                TEXT,
        status             INTEGER,
        impressum_url      TEXT,
        legal_name         TEXT,
        postal_address     TEXT,
        handelsregister    TEXT,
        vat_number         TEXT,
        phone              TEXT,
        email              TEXT,
        responsible_person TEXT,
        raw_json           JSONB,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql)
    finally:
        conn.close()


def fetch_target_companies(
    company: Optional[str],
    status: Optional[int],
    limit: Optional[int],
):
    conditions = []
    params = []

    if company:
        conditions.append("company_name = %s")
        params.append(company)
    if status is not None:
        conditions.append("status = %s")
        params.append(status)
    elif not company:
        conditions.append("(status IS NULL OR status <> %s)")
        params.append(int(CompanyStatus.OK))

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit_clause = "LIMIT %s" if limit is not None else ""
    if limit is not None:
        params.append(limit)

    query = f"SELECT id, company_name, web, status FROM companies {where_clause} ORDER BY id ASC {limit_clause};"

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, tuple(params))
            return cur.fetchall()
    finally:
        conn.close()


def update_status_only(company_id: int, status: CompanyStatus) -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE companies SET status = %s, updated_at = NOW() WHERE id = %s;",
                    (int(status), company_id),
                )
    finally:
        conn.close()


def update_enrichment_result(
    company_id: int,
    status: CompanyStatus,
    impressum_url: str,
    mapped: dict,
) -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE companies SET
                        status             = %s,
                        impressum_url      = %s,
                        legal_name         = %s,
                        postal_address     = %s,
                        handelsregister    = %s,
                        vat_number         = %s,
                        phone              = %s,
                        email              = %s,
                        responsible_person = %s,
                        raw_json           = %s,
                        updated_at         = NOW()
                    WHERE id = %s;
                    """,
                    (
                        int(status),
                        impressum_url,
                        mapped["legal_name"],
                        mapped["postal_address"],
                        mapped["handelsregister"],
                        mapped["vat_number"],
                        mapped["phone"],
                        mapped["email"],
                        mapped["responsible_person"],
                        Json(mapped["raw_json"]) if mapped["raw_json"] is not None else None,
                        company_id,
                    ),
                )
    finally:
        conn.close()


def process_company(row: dict, api_key: str, model: str) -> None:
    company_id = row["id"]
    company_name = row["company_name"]
    raw_web = row["web"]

    # 4.1 Normalize URL
    home_url = normalize_url(raw_web)
    if not home_url:
        logger.info("[%s] Empty web column -> status=%s (%s)", company_name, int(CompanyStatus.NO_WEBSITE), CompanyStatus.NO_WEBSITE.name)
        update_status_only(company_id, CompanyStatus.NO_WEBSITE)
        return

    # 4.2 Fetch home page and find Impressum link
    try:
        home_html, final_home_url = fetch_html(home_url)
    except requests.RequestException as exc:
        logger.error("[%s] Transport error fetching %s: %s -> status=%s (%s)", company_name, home_url, exc, int(CompanyStatus.ERROR), CompanyStatus.ERROR.name)
        update_status_only(company_id, CompanyStatus.ERROR)
        return

    impressum_url = find_impressum_link(home_html, final_home_url)
    if not impressum_url:
        logger.warning("[%s] No Impressum/Imprint link found at %s -> status=%s (%s)", company_name, final_home_url, int(CompanyStatus.NO_IMPRESSUM_LINK_FOUND), CompanyStatus.NO_IMPRESSUM_LINK_FOUND.name)
        update_status_only(company_id, CompanyStatus.NO_IMPRESSUM_LINK_FOUND)
        return

    time.sleep(0.5)  # politeness delay

    # 4.3 Fetch Impressum page and extract visible text
    try:
        impressum_html, resolved_impressum_url = fetch_html(impressum_url)
    except requests.RequestException as exc:
        logger.error("[%s] Transport error fetching Impressum %s: %s -> status=%s (%s)", company_name, impressum_url, exc, int(CompanyStatus.ERROR), CompanyStatus.ERROR.name)
        update_status_only(company_id, CompanyStatus.ERROR)
        return

    visible_text = extract_visible_text(impressum_html)

    # 4.4 Optimize text (token budget)
    optimized_text, stats = optimize_impressum_text(visible_text)
    logger.info(
        "[%s] Impressum=%s | chars: %d -> %d | est_tokens: %d -> %d | strategy=%s",
        company_name,
        resolved_impressum_url,
        stats["before_chars"],
        stats["after_chars"],
        stats["before_tokens"],
        stats["after_tokens"],
        stats["strategy"],
    )

    # 4.5 Call DeepSeek
    try:
        parsed_json = call_deepseek(optimized_text, api_key=api_key, model=model)
    except DeepSeekTransportError as exc:
        logger.error("[%s] DeepSeek API failure: %s -> status=%s (%s)", company_name, exc, int(CompanyStatus.ERROR), CompanyStatus.ERROR.name)
        update_status_only(company_id, CompanyStatus.ERROR)
        return

    # 4.6 Decide status & save
    status, mapped = evaluate_extraction(parsed_json, fallback_company_name=company_name)
    update_enrichment_result(company_id, status, resolved_impressum_url, mapped)
    logger.info("[%s] Saved status=%s (%s) | address=%s", company_name, int(status), status.name, mapped["postal_address"])


def main() -> int:
    load_dotenv_stdlib()

    parser = argparse.ArgumentParser(description="Enrich companies from PostgreSQL via Impressum + DeepSeek")
    parser.add_argument("--company", type=str, default=None, help="Process only this company_name")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N rows")
    parser.add_argument("--status", type=int, default=None, help="Process only rows with this status code")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        logger.error("DEEPSEEK_API_KEY environment variable is missing.")
        return 1

    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    ensure_schema()

    rows = fetch_target_companies(company=args.company, status=args.status, limit=args.limit)
    logger.info("Found %d company row(s) to process.", len(rows))

    for row in rows:
        process_company(row, api_key=api_key, model=model)
        time.sleep(0.5)

    return 0


if __name__ == "__main__":
    sys.exit(main())