"""Descarga reseñas de Amazon vía OpenWeb Ninja y deja el relay en data/amazon-reviews.json.

Sin cookie, Amazon solo enseña las ~8 reseñas "destacadas" de cada ficha: la API ignora
sort_by y no devuelve cursor (comprobado el 2026-11-17). Con la cookie de una cuenta de
comprador con sesión iniciada en ese marketplace (secret AMAZON_COOKIE_<MK>), se piden por
MOST_RECENT y se pagina con `cursor` hasta topar con una reseña ya vista.

Ficheros:
  data/reviews-store.json  todas las reseñas vistas alguna vez, con la fecha en que se vieron
                           por primera vez (sirve para saber cuándo parar de paginar).
  data/amazon-reviews.json lo que importa la rutina de Claude: las vistas por primera vez en
                           los últimos EXPORT_DAYS días. Ventana deslizante para que una semana
                           sin importar (conector caído, etc.) no pierda reseñas: el INSERT usa
                           ON CONFLICT DO NOTHING, así que repetir es inocuo.
  data/scan-status.json    resumen de la ejecución; el workflow falla si hubo errores.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.openwebninja.com/realtime-amazon-data/product-reviews"

# ASIN activos en tracked_products (Supabase, VULKKANO MKT). Actualizar a mano si cambian.
ASINS = [
    "B0GC7F88ND", "B0GC83XQNL", "B0C85L5WY8", "B0HHJJQGHD", "B0C823NPZY", "B0C821VHF7",
    "B0C8238XNZ", "B0CZNXX615", "B0HHJS9FW4", "B0FNRV2QP8", "B0FNX58MZP", "B0FDGKC68L",
    "B0HHJMLHP8", "B0GSGKGB96", "B0FDGDN7SG", "B09XHMJN4F", "B0DCNMHB5D", "B0FKHLK43H",
    # Añadidos 2026-11-17
    "B0HHJLT2BB", "B0HHJHLFKM",  # DESK blanco, negro
    "B0HNW3Z45H",                # SUB8s
    "B0HHJDFM7X", "B0HHJF41XS",  # TD10BT blanco, marrón
    "B0HHJDPL6Z", "B0HHJ9DKMQ",  # A6 ARC MKII marrón, blanco
]

# (código nuestro, país para la API, nombre en español)
MARKETS = [
    ("ES", "ES", "España"), ("DE", "DE", "Alemania"), ("FR", "FR", "Francia"),
    ("IT", "IT", "Italia"), ("UK", "GB", "Reino Unido"), ("NL", "NL", "Países Bajos"),
    ("BE", "BE", "Bélgica"), ("PL", "PL", "Polonia"), ("SE", "SE", "Suecia"),
    ("IE", "IE", "Irlanda"),
]

# Cuando una ficha tiene pocas reseñas locales, Amazon (con sesión iniciada) añade al final
# reseñas de otros países ("Recensito in Spagna il ..."). Son duplicados de otros
# marketplaces: solo nos quedamos con las que mencionan el país propio en la fecha.
LOCAL = {
    "ES": ["España"], "DE": ["Deutschland"], "FR": ["France"], "IT": ["Italia"],
    "UK": ["United Kingdom"], "NL": ["Nederland"], "BE": ["België", "Belgique", "Belgium"],
    "PL": ["Polsce"], "SE": ["Sverige"], "IE": ["Ireland"],
}


def is_local(review_date, mk):
    return not review_date or any(t in review_date for t in LOCAL[mk])


STORE = "data/reviews-store.json"
RELAY = "data/amazon-reviews.json"
STATUS = "data/scan-status.json"

KEY = os.environ["OPENWEBNINJA_KEY"]
MAX_PAGES = int(os.environ.get("MAX_PAGES") or 3)
BACKFILL = (os.environ.get("BACKFILL") or "").lower() == "true"
# El scan corre lunes, miércoles y viernes. Estos marketplaces (pocas reseñas, sin cookie)
# solo se consultan los lunes (o en una carga inicial) para no gastar llamadas.
WEEKLY_MARKETS = {"NL", "BE", "PL", "SE", "IE"}
EXPORT_DAYS = int(os.environ.get("EXPORT_DAYS") or 60)
ONLY_MK = {m.strip().upper() for m in (os.environ.get("ONLY_MARKETS") or "").split(",") if m.strip()}
ONLY = {a.strip() for a in (os.environ.get("ONLY_ASINS") or "").split(",") if a.strip()}

now = datetime.now(timezone.utc)
today = now.date().isoformat()


def call(params):
    """GET a la API con reintentos. Devuelve (json, error_str)."""
    url = API + "?" + urllib.parse.urlencode(params)
    last = ""
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"x-api-key": KEY})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.load(resp)
            if data.get("status") != "OK":
                last = f"status={data.get('status')} {str(data.get('error') or data.get('message') or '')[:150]}"
            else:
                return data, ""
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code != 404:
                try:
                    last += " " + e.read().decode("utf-8", "replace")[:200].replace("\n", " ")
                except Exception:
                    pass
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:  # timeouts, JSON corrupto...
            last = type(e).__name__
        time.sleep(2 * (attempt + 1))
    return None, last


def to_row(r, asin, mk, country):
    return {
        "asin": asin,
        "marketplace": mk,
        "review_id": r.get("review_id"),
        "title": r.get("review_title"),
        "body": r.get("review_comment"),
        "rating": r.get("review_star_rating"),
        "review_date_raw": r.get("review_date"),
        "country": country,
        "reviewer_name": r.get("review_author"),
        "verified_purchase": r.get("is_verified_purchase"),
        "review_link": r.get("review_link"),
        "is_vine": r.get("is_vine"),
    }


def load_store():
    if os.path.exists(STORE):
        with open(STORE, encoding="utf-8") as f:
            return json.load(f)
    # Primera ejecución: sembrar con el relay anterior para no reexportar como "nuevo"
    # lo que la rutina ya importó.
    store = {}
    if os.path.exists(RELAY):
        with open(RELAY, encoding="utf-8") as f:
            old = json.load(f)
        seen = (old.get("checkedAt") or today)[:10]
        for row in old.get("reviews", []):
            store[f"{row['marketplace']}:{row['review_id']}"] = {**row, "first_seen": seen}
    return store


def main():
    store = load_store()
    for key in [k for k, r in store.items() if not is_local(r.get("review_date_raw"), r["marketplace"])]:
        del store[key]  # limpia las de otros países que se colaron antes de este filtro
    errors, stats, not_listed = [], {}, []
    market_seen = {}  # por marketplace: ¿algún cursor? y máximo de valoraciones de una ficha
    dead_cookie = set()  # marketplaces cuya cookie ha rechazado Amazon en esta ejecución
    with_cookie = [mk for mk, _, _ in MARKETS if os.environ.get(f"AMAZON_COOKIE_{mk}", "").strip()]
    print(f"Marketplaces con cookie: {', '.join(with_cookie) or 'ninguno'} · max_pages={MAX_PAGES} backfill={BACKFILL}")

    for asin in ASINS:
        if ONLY and asin not in ONLY:
            continue
        for mk, api_country, country in MARKETS:
            if ONLY_MK and mk not in ONLY_MK:
                continue
            if mk in WEEKLY_MARKETS and now.weekday() != 0 and not BACKFILL:
                continue
            cookie = "" if mk in dead_cookie else os.environ.get(f"AMAZON_COOKIE_{mk}", "").strip()
            params = {"asin": asin, "country": api_country}
            if cookie:
                params.update(cookie=cookie, sort_by="MOST_RECENT")
            cursor, pages, new_here = None, 0, 0
            while True:
                if cursor:
                    params["cursor"] = cursor
                data, err = call(params)
                pages += 1
                if err == "HTTP 404":
                    not_listed.append(f"{asin} {mk}")  # el ASIN no se vende en ese Amazon
                    break
                if err and cookie and pages == 1:
                    # Con cookie, la API responde 503 (no 404) para fichas que no existen en ese
                    # Amazon, y 400 "different cookie" si Amazon ha anulado la sesión (amazon.fr
                    # lo hace tras ~10 consultas). Repetimos sin cookie: aclara el 404 y, si la
                    # cookie ha muerto, al menos guardamos las reseñas destacadas como antes.
                    if "different cookie" in err:
                        dead_cookie.add(mk)  # no la usamos más en esta ejecución
                    data2, err2 = call({"asin": asin, "country": api_country})
                    if err2 == "HTTP 404":
                        not_listed.append(f"{asin} {mk}")
                        break
                    if not err2 and mk in dead_cookie:
                        data, err, cookie = data2, "", ""
                if err:
                    errors.append(f"{asin} {mk} p{pages}: {err}")
                    break
                d = data.get("data") or {}
                reviews = d.get("reviews") or []
                hit_known = foreign = False
                for r in reviews:
                    if not r.get("review_id"):
                        continue
                    if not is_local(r.get("review_date"), mk):
                        foreign = True  # a partir de aquí ya no quedan reseñas locales
                        continue
                    key = f"{mk}:{r['review_id']}"
                    if key in store:
                        hit_known = True
                        continue
                    store[key] = {**to_row(r, asin, mk, country), "first_seen": today}
                    new_here += 1
                cursor = d.get("cursor")
                m = market_seen.setdefault(mk, {"cursor": False, "max_ratings": 0})
                m["cursor"] |= bool(cursor)
                m["max_ratings"] = max(m["max_ratings"], d.get("total_ratings") or 0)
                if not cookie or not cursor or foreign or pages >= MAX_PAGES or (hit_known and not BACKFILL):
                    break
                time.sleep(0.3)
            s = stats.setdefault(mk, {"calls": 0, "new": 0})
            s["calls"] += pages
            s["new"] += new_here

    # Con cookie válida, las fichas con muchas valoraciones devuelven cursor. Si en todo un
    # marketplace no ha salido ninguno, Amazon nos ha tratado como anónimos: cookie caducada.
    for mk in sorted(dead_cookie):
        errors.append(f"{mk}: Amazon ha rechazado la cookie; se han usado las reseñas destacadas sin cookie. Renovar AMAZON_COOKIE_{mk}")
    for mk in with_cookie:
        if mk in dead_cookie:
            continue
        m = market_seen.get(mk)
        if m and not m["cursor"] and m["max_ratings"] > 40:
            errors.append(f"{mk}: ninguna ficha devolvió cursor con cookie — probablemente caducada, renovar AMAZON_COOKIE_{mk}")

    cutoff = (now - timedelta(days=EXPORT_DAYS)).date().isoformat()
    export = [
        {k: v for k, v in row.items() if k != "first_seen"}
        for row in store.values()
        if row["first_seen"] >= cutoff
    ]
    os.makedirs("data", exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=0, sort_keys=True)
    with open(RELAY, "w", encoding="utf-8") as f:
        json.dump({"checkedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "reviews": export}, f, ensure_ascii=False)
    status = {
        "checkedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "markets_with_cookie": with_cookie,
        "new_reviews": sum(s["new"] for s in stats.values()),
        "exported": len(export),
        "stored_total": len(store),
        "by_market": stats,
        "not_listed": not_listed,
        "errors": errors,
    }
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    lines = [
        f"### Reseñas: {status['new_reviews']} nuevas · {len(export)} en el relay · {len(store)} conocidas",
        f"Cookie en: {', '.join(with_cookie) or 'ninguno (solo top 8 por ficha)'}",
        "",
        "| Marketplace | Llamadas | Nuevas |",
        "|---|---|---|",
        *[f"| {mk} | {s['calls']} | {s['new']} |" for mk, s in stats.items()],
    ]
    if not_listed:
        lines += ["", f"Sin ficha en ese Amazon (404): {', '.join(not_listed)}"]
    if errors:
        lines += ["", f"**{len(errors)} errores:**", *[f"- {e}" for e in errors[:50]]]
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
