"""
fetch_news.py
Fetches Algerian news headlines from RSS feeds (French, Arabic, and mixed sources),
translates titles to English, categorizes each story, and maintains a rolling
7-day window of up to 20 stories per category.
Output: docs/algeria_news.json
"""

import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
from dateutil import parser as dateparser

# ── Optional: language detection & translation ────────────────────────────────
try:
    from deep_translator import GoogleTranslator
    from langdetect import detect as lang_detect, LangDetectException
    TRANSLATION_AVAILABLE = True
except ImportError:
    TRANSLATION_AVAILABLE = False
    print("[WARN] Translation libraries not available; titles will not be translated.")

# ── Configuration ─────────────────────────────────────────────────────────────

OUTPUT_PATH = Path("docs/algeria_news.json")
MAX_STORIES_PER_CATEGORY = 20
MAX_AGE_DAYS = 7

FEEDS = [
    # French-language sources
    {"source": "TSA Algérie",            "url": "https://www.tsa-algerie.com/feed/",         "lang": "fr"},
    {"source": "El Watan",               "url": "https://www.elwatan.dz/feed/",              "lang": "fr"},
    {"source": "Algerie360",             "url": "https://www.algerie360.com/feed/",           "lang": "fr"},
    {"source": "Liberté Algérie",        "url": "https://www.liberte-algerie.com/feed",       "lang": "fr"},
    # Arabic-language sources
    {"source": "El Khabar",             "url": "https://www.elkhabar.com/press/feed/",       "lang": "ar"},
    {"source": "Echorouk Online",        "url": "https://www.echoroukonline.com/feed",        "lang": "ar"},
    {"source": "Ennahar Online",         "url": "https://www.ennaharonline.com/feed/",        "lang": "ar"},
    {"source": "APS (Algérie Presse)",   "url": "https://www.aps.dz/fr/?format=feed&type=rss", "lang": "fr"},
    # Sports / mixed
    {"source": "DzFoot",                 "url": "https://www.dzfoot.com/feed",               "lang": "fr"},
]

CATEGORIES = ["Diplomacy", "Military", "Energy", "Economy", "Local Events"]

# ── Keyword maps (multilingual: French + Arabic transliterations + English) ───

CATEGORY_KEYWORDS = {
    "Diplomacy": [
        # English
        r"\bdiplomat\w*\b", r"\bambassador\b", r"\btreaty\b", r"\bsanction\w*\b",
        r"\bforeign (affairs|minister|policy|relations)\b", r"\bnato\b",
        r"\bunited nations\b", r"\bun\b", r"\bembassy\b", r"\bconsulate\b",
        r"\btrade (deal|agreement|talks|negotiation)\b", r"\bsummit\b",
        r"\bpeace (deal|talks|process|treaty)\b", r"\bbilateral\b",
        r"\bmultilateral\b", r"\bG7\b", r"\bG20\b", r"\bimf\b", r"\bwto\b",
        r"\balgeria.*(usa?|china|russia|europe|eu|france|italy|morocco|mali|niger|libya)\b",
        r"\btebboune.*(visit|summit|meeting|trip|visite)\b",
        r"\battaf\b",  # Foreign Minister Ahmed Attaf
        # French
        r"\bdiplomati\w*\b", r"\bambassadeur\b", r"\btraité\b", r"\bsanction\w*\b",
        r"\baffaires étrangères\b", r"\bministre des affaires\b",
        r"\baccord (bilatéral|commercial|de paix)\b", r"\bsommet\b",
        r"\brelations (internationales|diplomatiques|bilatérales)\b",
        r"\bnations unies\b", r"\bonu\b", r"\bconsulat\b", r"\bambasad\w*\b",
        r"\bpolitique étrangère\b", r"\bnégociation\w*\b",
        r"\bcoopération (internationale|bilatérale|économique)\b",
        # Arabic transliteration keywords
        r"\bdiblomasiy\w*\b", r"\bsafara\b", r"\bwizarat al-kharijiy\w*\b",
        r"\batraf\b", r"\bmufawada\w*\b",
    ],
    "Military": [
        # English
        r"\bmilitary\b", r"\bdefence\b", r"\bdefense\b", r"\bsoldier\w*\b",
        r"\btroops?\b", r"\bnavy\b", r"\barmy\b", r"\bair force\b",
        r"\bweapon\w*\b", r"\bwarship\b", r"\bdrone\b", r"\bwar\b",
        r"\bconflict\b", r"\bbattle\b", r"\bcombat\b", r"\bterror\w*\b",
        r"\bnational security\b", r"\bintelligence\b", r"\bexplosion\b",
        r"\bmunition\w*\b", r"\bsecurity (forces|operation|threat)\b",
        r"\bpeacekeep\w*\b", r"\bdeployment\b",
        # French
        r"\barmée\b", r"\barmée nationale populaire\b", r"\banp\b",
        r"\bdéfense (nationale|militaire)?\b", r"\bministère de la défense\b",
        r"\bmdn\b", r"\bgendarmer\w*\b", r"\bterrorism\w*\b", r"\bterroriste\w*\b",
        r"\bsécurité (nationale|militaire|intérieure)\b", r"\bopération militaire\b",
        r"\bsoldats?\b", r"\barmes?\b", r"\bguerre\b", r"\bconflit\b",
        r"\bdéploiement\b", r"\bchef d'état-major\b", r"\bsaïd chanegriha\b",
        r"\bbrigade\b", r"\bbataillon\b", r"\bfrontière (militaire|sécurisée)\b",
        # Arabic / Sahel context
        r"\bjihad\b", r"\bjihadiste\w*\b", r"\bgroupes armés\b",
        r"\baqmi\b", r"\bgspc\b", r"\bmali (sécurité|force|armée)\b",
        r"\bniger (sécurité|armée|force)\b",
    ],
    "Energy": [
        # English
        r"\benergy\b", r"\boil\b", r"\bnatural gas\b", r"\bpipeline\b",
        r"\blng\b", r"\brenewable\b", r"\bsolar\b", r"\bwind (power|energy|farm)\b",
        r"\bhydro\b", r"\bnuclear\b", r"\belectricit\w*\b", r"\bpower (grid|plant)\b",
        r"\bcarbon\b", r"\bclimate\b", r"\bemission\w*\b", r"\bfuel\b",
        r"\bsonatrach\b", r"\bnaphtha\b", r"\bhydrocarbon\w*\b",
        r"\bgas (field|export|price|supply)\b", r"\bpetroleum\b",
        r"\benr\b",  # Energies Renouvelables
        # French
        r"\bénergie\b", r"\bpétrole\b", r"\bgaz naturel\b", r"\bgaz\b",
        r"\bgazoduc\b", r"\bpipeline\b", r"\bhydrocarbure\w*\b",
        r"\bénergies renouvelables\b", r"\bsolaire\b", r"\béolien\w*\b",
        r"\bnucléaire\b", r"\bélectricité\b", r"\bsonelgaz\b",
        r"\bsonatrach\b", r"\bnaftal\b", r"\bgisement\w*\b",
        r"\breffinerie\b", r"\braf(f)?inerie\b", r"\bmines?\b",
        r"\btransition énergétique\b", r"\bémission\w*\b", r"\bclimat\b",
        r"\bcarbone\b", r"\bprix du pétrole\b", r"\bforage\b",
        r"\bexploration (pétrolière|gazière)?\b",
    ],
    "Economy": [
        # English
        r"\beconom\w*\b", r"\bbudget\b", r"\bgdp\b", r"\binflation\b",
        r"\binterest rate\b", r"\brecession\b", r"\btariff\w*\b",
        r"\btrade (war|deficit|surplus|balance)\b", r"\bjob\w*\b",
        r"\bunemployment\b", r"\blabou?r\b", r"\bwage\w*\b",
        r"\bhousing (market|price|crisis)\b", r"\breal estate\b",
        r"\bexport\w*\b", r"\bimport\w*\b", r"\bcost of living\b",
        r"\bfood (price|security)\b", r"\btax\w*\b", r"\bfiscal\b",
        r"\bimf\b", r"\bworld bank\b", r"\bforeign (investment|exchange|currency)\b",
        r"\bdinar\b",
        # French
        r"\béconom\w*\b", r"\bbudget\b", r"\bpib\b", r"\binflation\b",
        r"\btaux (d'intérêt|de change|de croissance|de chômage)\b",
        r"\bchômage\b", r"\bdéficit\b", r"\bexportation\w*\b", r"\bimportation\w*\b",
        r"\bcommerce (extérieur|international)?\b", r"\bbalance commerciale\b",
        r"\bdinars?\b", r"\bdevises?\b", r"\binvestissement\w*\b",
        r"\bfinance (publique|nationale|internationale)?\b",
        r"\bministère des finances\b", r"\bbanque (d'algérie|centrale|mondiale)\b",
        r"\bcroissance économique\b", r"\brecettes (fiscales|pétrolières)?\b",
        r"\bmarché (noir|parallèle|de change|boursier)\b",
        r"\bprix (à la consommation|de détail|des denrées)?\b",
        r"\bimport-substitution\b", r"\bplan (de développement|économique)?\b",
        r"\bdouane\w*\b", r"\bfmi\b",
    ],
    "Local Events": [
        # English
        r"\bcommunity\b", r"\btown hall\b", r"\bfestival\b", r"\bparade\b",
        r"\bfire\b", r"\bflood\b", r"\baccident\b", r"\bcrash\b",
        r"\bcrime\b", r"\barrest\b", r"\bpolice\b", r"\bcourt\b",
        r"\bmayor\b", r"\bmunicip\w*\b", r"\bschool\b", r"\buniversity\b",
        r"\bhospital\b", r"\bweather\b", r"\bstorm\b", r"\bwildfire\b",
        r"\bdrought\b", r"\bsports?\b", r"\bculture\b", r"\barts?\b",
        r"\bheritage\b", r"\bcelebration\b", r"\bholiday\b",
        r"\binfrastructure\b", r"\btransit\b",
        # French
        r"\bcommune\b", r"\bwilaya\b", r"\bdaïra\b", r"\bmairie\b",
        r"\bmaire\b", r"\bélu\w*\b", r"\bcollectivité\w*\b",
        r"\bincendie\b", r"\binondation\b", r"\baccident\b", r"\bcrash\b",
        r"\bdrame\b", r"\bvictime\w*\b", r"\bpolice\b", r"\bgendarmerie\b",
        r"\btribunal\b", r"\bjugement\b", r"\bcondamné\b", r"\barrêté\b",
        r"\bécole\b", r"\buniversité\b", r"\bhôpital\b", r"\bsanté\b",
        r"\bculture\b", r"\bsport\b", r"\bfestival\b", r"\bfête\b",
        r"\bramadhane?\b", r"\baïd\b", r"\bpèlerinage\b", r"\bhaj\w*\b",
        r"\binfrastructure\b", r"\broute\b", r"\btransport\b",
        r"\bsécheresse\b", r"\bintempérie\w*\b", r"\bneige\b",
        r"\bincendies de forêt\b", r"\bséisme\b", r"\btremblement de terre\b",
    ],
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                import calendar
                ts = calendar.timegm(t)
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                dt = dateparser.parse(raw)
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
    return None


def detect_language(text: str) -> str:
    """Detect language of text, return ISO 639-1 code or 'unknown'."""
    if not TRANSLATION_AVAILABLE or not text:
        return "unknown"
    try:
        return lang_detect(text)
    except LangDetectException:
        return "unknown"


def translate_to_english(text: str, source_lang: str = "auto") -> str:
    """Translate text to English. Returns original text on failure."""
    if not TRANSLATION_AVAILABLE or not text:
        return text
    # Skip if already English
    if source_lang == "en":
        return text
    detected = detect_language(text)
    if detected == "en":
        return text
    # Only translate French and Arabic (the two languages of Algerian media)
    if detected not in ("fr", "ar") and source_lang not in ("fr", "ar"):
        return text
    try:
        src = detected if detected in ("fr", "ar") else source_lang
        if src not in ("fr", "ar"):
            src = "auto"
        translated = GoogleTranslator(source=src, target="en").translate(text)
        return translated if translated else text
    except Exception as exc:
        print(f"[WARN] Translation failed for '{text[:60]}': {exc}")
        return text


def score_category(text: str) -> str:
    """Return the best-matching category for given text (English or French or Arabic)."""
    text_lower = text.lower()
    scores = {cat: 0 for cat in CATEGORIES}
    for cat, patterns in CATEGORY_KEYWORDS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                scores[cat] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Local Events"


def fetch_feed(source: str, url: str, declared_lang: str) -> list[dict]:
    """Fetch one RSS/Atom feed and return a list of normalised story dicts."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; StratagemdrivBot/1.0; "
            "+https://stratagemdrive.github.io/algeria-local/)"
        ),
        "Accept-Language": "fr-DZ,fr;q=0.9,ar;q=0.8,en;q=0.7",
    }
    stories = []
    cutoff = now_utc() - timedelta(days=MAX_AGE_DAYS)

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:
        print(f"[WARN] Could not fetch {url}: {exc}")
        return stories

    for entry in feed.entries:
        pub_dt = parse_date(entry)
        if pub_dt is None:
            continue
        if pub_dt < cutoff:
            continue

        raw_title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not raw_title or not link:
            continue

        summary = entry.get("summary") or entry.get("description") or ""

        # Translate title to English for display and categorisation
        english_title = translate_to_english(raw_title, source_lang=declared_lang)

        # Categorise using English title + translated summary snippet
        summary_snippet = summary[:300] if summary else ""
        english_summary = translate_to_english(summary_snippet, source_lang=declared_lang)
        category = score_category(f"{english_title} {english_summary}")

        stories.append({
            "title":          english_title,
            "source":         source,
            "url":            link,
            "published_date": pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "category":       category,
        })

        # Small delay between translation API calls to avoid rate-limiting
        if TRANSLATION_AVAILABLE and raw_title != english_title:
            time.sleep(0.3)

    return stories


def load_existing() -> dict[str, list[dict]]:
    if OUTPUT_PATH.exists():
        try:
            with OUTPUT_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "stories" in data:
                by_cat: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
                for story in data["stories"]:
                    cat = story.get("category")
                    if cat in by_cat:
                        by_cat[cat].append(story)
                return by_cat
        except Exception as exc:
            print(f"[WARN] Could not parse existing JSON: {exc}")
    return {c: [] for c in CATEGORIES}


def merge_stories(
    existing: dict[str, list[dict]],
    fresh: list[dict],
) -> dict[str, list[dict]]:
    cutoff = now_utc() - timedelta(days=MAX_AGE_DAYS)

    # Prune expired entries
    for cat in CATEGORIES:
        existing[cat] = [
            s for s in existing[cat]
            if dateparser.parse(s["published_date"]).replace(tzinfo=timezone.utc) >= cutoff
        ]

    # Build known-URL sets
    known_urls: dict[str, set[str]] = {
        cat: {s["url"] for s in existing[cat]} for cat in CATEGORIES
    }

    # Insert new stories
    for story in fresh:
        cat = story["category"]
        if story["url"] in known_urls.get(cat, set()):
            continue
        existing[cat].append(story)
        known_urls[cat].add(story["url"])

    # Sort descending and cap at MAX_STORIES_PER_CATEGORY
    for cat in CATEGORIES:
        existing[cat].sort(key=lambda s: s["published_date"], reverse=True)
        existing[cat] = existing[cat][:MAX_STORIES_PER_CATEGORY]

    return existing


def write_output(by_cat: dict[str, list[dict]]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_stories = [s for stories in by_cat.values() for s in stories]
    payload = {
        "generated_at":  now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "country":        "Algeria",
        "total_stories":  len(all_stories),
        "categories":     CATEGORIES,
        "stories":        all_stories,
    }
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[INFO] Wrote {len(all_stories)} stories to {OUTPUT_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[INFO] Starting Algeria news fetch at {now_utc().isoformat()}")
    if not TRANSLATION_AVAILABLE:
        print("[WARN] deep-translator / langdetect not installed. Titles will not be translated.")

    fresh_stories: list[dict] = []
    for feed_cfg in FEEDS:
        print(f"[INFO] Fetching {feed_cfg['source']} ({feed_cfg['lang']}) → {feed_cfg['url']}")
        stories = fetch_feed(feed_cfg["source"], feed_cfg["url"], feed_cfg["lang"])
        print(f"       Found {len(stories)} recent stories")
        fresh_stories.extend(stories)

    print(f"[INFO] Total fresh stories collected: {len(fresh_stories)}")

    existing = load_existing()
    merged   = merge_stories(existing, fresh_stories)
    write_output(merged)


if __name__ == "__main__":
    main()
