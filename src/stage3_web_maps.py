"""Stage 3 (maps-first pivot) -- website + contact enrichment for
`maps_companies` rows.

Repointed from the old DBPR-first src/stage3_web.py (see
docs/superpowers/specs/2026-08-28-maps-first-north-florida-design.md,
"Stage 3/4 pivot" section) at maps_companies instead of companies. The
fetch/parse logic (robots.txt respect, 1 req/sec pacing, 10s timeout, email
extraction from homepage + contact/about pages, owner/founder name
extraction, booking/chat widget detection, 24/7-emergency mention, and
form-only-contact detection) is reused unmodified from enrich_website() below
-- only the source table, the excluded-row filter (is_duplicate /
lead_mill_suspect), and the DBPR cross-check field name
(matched_principal_name instead of principal_name) changed.

Excludes rows where is_duplicate=1 OR lead_mill_suspect=1 (dedup/lead-mill
flags added by the Stage-2.5 pass, see db.DEDUP_COLUMNS).

No website -> row stays with all contact fields null (Tier-3 equivalent),
not skipped. No email found but domain known -> guessed email
(first@domain / firstlast@domain if an owner name was found, else
info@domain), email_source='guessed'.

Idempotent/resumable: only processes rows with stage3_processed_at IS NULL,
so re-running (e.g. after an interrupted batch) skips already-done rows.
Use --limit N to smoke-test on a handful of rows.
"""
from __future__ import annotations

import json
import re
import time
import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import db

UA = "Mozilla/5.0 (compatible; JLMoldFLResearchBot/1.0; +https://jobslocked.com/bot)"
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
CHAT_HINTS = ["intercom", "drift.com", "tawk.to", "livechat", "zendesk", "hubspot", "crisp.chat", "tidio"]
BOOKING_HINTS = ["calendly", "acuityscheduling", "housecallpro", "jobber.com", "servicetitan",
                 "schedule now", "book now", "book online", "book an appointment"]
EMERGENCY_HINTS = ["24/7", "24-7", "247 emergency", "emergency service", "available 24 hours"]

_robots_cache: dict[str, robotparser.RobotFileParser] = {}


def _allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in _robots_cache:
            rp = robotparser.RobotFileParser()
            rp.set_url(urljoin(base, "/robots.txt"))
            try:
                rp.read()
            except Exception:
                pass
            _robots_cache[base] = rp
        return _robots_cache[base].can_fetch(UA, url)
    except Exception:
        return True


def _fetch(conn, url: str) -> str | None:
    cache_key = f"web_{url}"
    cached = db.cache_get(conn, cache_key)
    if cached is not None:
        obj = json.loads(cached)
        return obj.get("html")
    if not _allowed(url):
        db.cache_put(conn, cache_key, "web_fetch", json.dumps({"html": None, "blocked": True}))
        return None
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=10)
        html = resp.text if resp.status_code == 200 else None
    except requests.RequestException:
        html = None
    db.cache_put(conn, cache_key, "web_fetch", json.dumps({"html": html}))
    time.sleep(1.0)  # 1 req/sec
    return html


def _find_subpage(soup: BeautifulSoup, base_url: str, keywords: list[str]) -> str | None:
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(k in href for k in keywords):
            return urljoin(base_url, a["href"])
    return None


def enrich_website(conn, url: str, principal_name: str | None) -> dict:
    out = {"email": None, "email_source": None, "owner_name_found": None, "owner_confirmed": 0,
           "booking_widget": 0, "chat_widget": 0, "emergency_247": 0, "form_only_contact": 0}
    html = _fetch(conn, url)
    if not html:
        return out
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    full_text = text
    full_html = html.lower()

    contact_url = _find_subpage(soup, url, ["contact", "about"])
    if contact_url:
        chtml = _fetch(conn, contact_url)
        if chtml:
            csoup = BeautifulSoup(chtml, "lxml")
            full_text += " " + csoup.get_text(" ", strip=True)
            full_html += chtml.lower()

    emails = EMAIL_RE.findall(full_text + " " + full_html)
    emails = [e for e in emails if not e.lower().endswith((".png", ".jpg", ".svg", ".gif"))]
    if emails:
        out["email"] = sorted(set(emails), key=len)[0]
        out["email_source"] = "found"

    out["booking_widget"] = int(any(h in full_html for h in BOOKING_HINTS))
    out["chat_widget"] = int(any(h in full_html for h in CHAT_HINTS))
    out["emergency_247"] = int(any(h in full_text.lower() for h in EMERGENCY_HINTS))
    forms = len(soup.find_all("form"))
    tel_links = len(soup.select('a[href^="tel:"]'))
    out["form_only_contact"] = int(forms > 0 and tel_links == 0 and not out["email"])

    if principal_name:
        pn_tokens = [t for t in re.split(r"[,\s]+", principal_name.strip()) if len(t) > 2]
        hits = sum(1 for t in pn_tokens if t.lower() in full_text.lower())
        if pn_tokens and hits >= max(1, len(pn_tokens) - 1):
            out["owner_confirmed"] = 1
            out["owner_name_found"] = principal_name

    if not out["owner_name_found"]:
        m = re.search(r"(?:owner|founder|president)[:\s\-]{1,4}([A-Z][a-zA-Z.'-]+\s+[A-Z][a-zA-Z.'-]+)", full_text)
        if m:
            out["owner_name_found"] = m.group(1)

    # No live email found but domain known -> generate guesses. Prefer
    # first@/firstlast@ using an owner/founder name if we found one,
    # otherwise fall back to info@domain (same fallback as old stage3_web).
    if not out["email"]:
        domain = urlparse(url).netloc.replace("www.", "")
        if domain:
            if out["owner_name_found"]:
                parts = [p for p in re.split(r"\s+", out["owner_name_found"].strip()) if p]
                if len(parts) >= 2:
                    first, last = parts[0].lower(), parts[-1].lower()
                    first = re.sub(r"[^a-z]", "", first)
                    last = re.sub(r"[^a-z]", "", last)
                    guess = f"{first}{last}@{domain}" if first and last else f"info@{domain}"
                else:
                    guess = f"info@{domain}"
            else:
                guess = f"info@{domain}"
            out["email"] = guess
            out["email_source"] = "guessed"

    return out


def run(limit: int | None = None, ids: list[int] | None = None) -> dict:
    conn = db.get_connection()
    db.run_maps_companies_migration(conn)
    db.run_dedup_migration(conn)
    db.run_maps_enrich_migrations(conn)

    query = (
        "SELECT * FROM maps_companies "
        "WHERE COALESCE(is_duplicate, 0) != 1 AND COALESCE(lead_mill_suspect, 0) != 1 "
        "AND stage3_processed_at IS NULL "
    )
    if ids:
        query += f"AND id IN ({','.join('?' * len(ids))}) "
    query += "ORDER BY id"
    rows = conn.execute(query, tuple(ids) if ids else ()).fetchall()
    if limit:
        rows = rows[:limit]

    processed = 0
    with_site = 0
    emails_found = 0
    emails_guessed = 0
    for row in rows:
        site = row["website"]
        now = db._now()
        if not site:
            conn.execute(
                "UPDATE maps_companies SET stage3_processed_at=? WHERE id=?",
                (now, row["id"]),
            )
            conn.commit()
            processed += 1
            continue
        with_site += 1
        res = enrich_website(conn, site, row["matched_principal_name"])
        conn.execute(
            """UPDATE maps_companies SET email=?, email_source=?, owner_name_found=?,
               owner_confirmed=?, booking_widget=?, chat_widget=?, emergency_247=?,
               form_only_contact=?, stage3_processed_at=? WHERE id=?""",
            (res["email"], res["email_source"], res["owner_name_found"], res["owner_confirmed"],
             res["booking_widget"], res["chat_widget"], res["emergency_247"], res["form_only_contact"],
             now, row["id"]),
        )
        conn.commit()
        if res["email_source"] == "found":
            emails_found += 1
        elif res["email_source"] == "guessed":
            emails_guessed += 1
        processed += 1
        if processed % 100 == 0:
            print(f"stage3_web_maps: {processed}/{len(rows)} (sites={with_site}, found={emails_found}, guessed={emails_guessed})")

    conn.close()
    summary = {"processed": processed, "with_website": with_site,
               "emails_found": emails_found, "emails_guessed": emails_guessed,
               "email_hit_rate": round((emails_found + emails_guessed) / with_site, 4) if with_site else 0}
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--ids", type=str, default=None, help="comma-separated maps_companies ids, for smoke testing")
    a = p.parse_args()
    run(limit=a.limit, ids=[int(x) for x in a.ids.split(",")] if a.ids else None)
