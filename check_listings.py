"""
Sprawdza podane wyszukiwania na OLX / Allegro / Allegro Lokalnie / Vinted
i wysyła powiadomienie push na telefon (przez ntfy.sh) gdy pojawią się nowe
ogłoszenia. Jeśli w jednym uruchomieniu pojawi się kilka nowych ofert naraz,
są grupowane w jedno zbiorcze powiadomienie zamiast spamu.

Strony pobierane są prawdziwą, headless przeglądarką (Playwright/Chromium),
a nie zwykłym requests.get — te serwisy blokują (403) proste zapytania HTTP
z adresów IP centrów danych (jak GitHub Actions), a przeglądarka wygląda
znacznie bardziej jak prawdziwy użytkownik. To i tak nie daje 100% gwarancji
ominięcia blokady — jeśli dana platforma nadal zwraca 0 wyników mimo braku
błędu, to znak, że trzeba dostroić selektor albo rozważyć uruchamianie bota
z domowego łącza zamiast z GitHub Actions.

Stan "co już widzieliśmy" trzymany jest w data/seen.json — ten plik jest
commitowany z powrotem do repo przez GitHub Actions po każdym uruchomieniu,
dzięki czemu bot pamięta co już zgłosił między kolejnymi odpaleniami.
"""

import json
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
SEEN_PATH = ROOT / "data" / "seen.json"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}" if NTFY_TOPIC else None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

PAGE_LOAD_TIMEOUT_MS = 30000
EXTRA_WAIT_MS = 2500  # dodatkowy czas na doładowanie treści przez JS


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def send_notification(title, message, click_link=None):
    if not NTFY_URL:
        print("BRAK NTFY_TOPIC — pomijam wysyłkę, tylko loguję:")
        print(f"{title}\n{message}")
        return
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": "default",
        "Tags": "new",
    }
    if click_link:
        headers["Click"] = click_link
    try:
        resp = requests.post(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"Błąd wysyłki do ntfy: {resp.status_code} {resp.text}")
    except requests.RequestException as e:
        print(f"Wyjątek przy wysyłce do ntfy: {e}")


# ---------------------------------------------------------------------------
# Pobieranie strony prawdziwą przeglądarką (Playwright).
# ---------------------------------------------------------------------------

def render_html(browser, url, wait_selector=None, extra_wait_ms=EXTRA_WAIT_MS):
    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="pl-PL",
        viewport={"width": 1366, "height": 900},
    )
    page = context.new_page()
    html = None
    try:
        page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=PAGE_LOAD_TIMEOUT_MS)
            except Exception:
                # Selektor mógł się nie pojawić (np. brak wyników albo strona
                # zmieniła markup) — i tak bierzemy to, co jest na stronie.
                pass
        page.wait_for_timeout(extra_wait_ms)
        html = page.content()
    except Exception as e:
        print(f"  Błąd renderowania {url}: {e}")
    finally:
        context.close()
    return html


# ---------------------------------------------------------------------------
# Parsery dla poszczególnych platform — operują na już wyrenderowanym HTML.
# UWAGA: struktura tych stron zmienia się co jakiś czas — jeśli bot przestanie
# znajdować ogłoszenia mimo braku błędów, prawdopodobnie trzeba będzie
# zaktualizować selektory poniżej (sprawdź "Narzędzia deweloperskie" w
# przeglądarce → zakładka Elements na stronie wyników).
# ---------------------------------------------------------------------------

def parse_olx(html):
    items = []
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("div[data-cy='l-card']")
    for card in cards:
        link_tag = card.select_one("a")
        title_tag = card.select_one("h4, h6")
        price_tag = card.select_one("[data-testid='ad-price']")
        if not link_tag or not link_tag.get("href"):
            continue
        href = link_tag["href"]
        if href.startswith("/"):
            href = "https://www.olx.pl" + href
        items.append({
            "id": href.split("?")[0],
            "title": title_tag.get_text(strip=True) if title_tag else "(brak tytułu)",
            "price": price_tag.get_text(strip=True) if price_tag else "",
            "link": href,
        })
    return items


def parse_allegro(html):
    items = []
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("article[data-role='offer']") or soup.select("article")
    for card in cards:
        link_tag = card.select_one("a[href*='/oferta/']")
        title_tag = card.select_one("h2, [data-role='title']")
        price_tag = card.select_one("[data-role='price'], span[aria-label*='zł']")
        if not link_tag or not link_tag.get("href"):
            continue
        href = link_tag["href"].split("?")[0]
        items.append({
            "id": href,
            "title": title_tag.get_text(strip=True) if title_tag else "(brak tytułu)",
            "price": price_tag.get_text(strip=True) if price_tag else "",
            "link": href,
        })
    return items


def parse_allegro_lokalnie(html):
    items = []
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("article") or soup.select("a[href*='/oferty/']")
    seen_hrefs = set()
    for card in cards:
        link_tag = card if card.name == "a" else card.select_one("a[href*='/oferty/']")
        title_tag = card.select_one("h2, h3, [class*='title']")
        price_tag = card.select_one("[class*='price']")
        if not link_tag or not link_tag.get("href"):
            continue
        href = link_tag["href"].split("?")[0]
        if href.startswith("/"):
            href = "https://allegrolokalnie.pl" + href
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        items.append({
            "id": href,
            "title": title_tag.get_text(strip=True) if title_tag else "(brak tytułu)",
            "price": price_tag.get_text(strip=True) if price_tag else "",
            "link": href,
        })
    return items


def parse_vinted(html):
    items = []
    soup = BeautifulSoup(html, "lxml")
    # Karty ogłoszeń na Vinted zwykle mają atrybut data-testid zawierający
    # "item" lub "grid-item" — łapiemy oba warianty na wszelki wypadek.
    cards = (
        soup.select("[data-testid*='grid-item']")
        or soup.select("[data-testid*='item-box']")
        or soup.select("a[href*='/items/']")
    )
    seen_hrefs = set()
    for card in cards:
        link_tag = card if card.name == "a" else card.select_one("a[href*='/items/']")
        if not link_tag or not link_tag.get("href"):
            continue
        href = link_tag["href"].split("?")[0]
        if href.startswith("/"):
            href = "https://www.vinted.pl" + href
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        title_tag = card.select_one("[data-testid*='title']") or card.select_one("p, h3")
        price_tag = card.select_one("[data-testid*='price']")
        items.append({
            "id": href,
            "title": title_tag.get_text(strip=True) if title_tag else "(brak tytułu)",
            "price": price_tag.get_text(strip=True) if price_tag else "",
            "link": href,
        })
    return items


# Selektor, na który warto poczekać przed pobraniem HTML (żeby JS zdążył
# doładować listę ofert) — None oznacza "po prostu poczekaj EXTRA_WAIT_MS".
WAIT_SELECTORS = {
    "olx": "div[data-cy='l-card']",
    "allegro": "article",
    "allegro_lokalnie": "article",
    "vinted": "[data-testid*='grid-item'], a[href*='/items/']",
}

PARSERS = {
    "olx": parse_olx,
    "allegro": parse_allegro,
    "allegro_lokalnie": parse_allegro_lokalnie,
    "vinted": parse_vinted,
}


MAX_ITEMS_IN_MESSAGE = 15  # żeby powiadomienie nie zrobiło się absurdalnie długie


def main():
    config = load_json(CONFIG_PATH, {"searches": []})
    seen = load_json(SEEN_PATH, {})

    all_new = []  # lista (search_name, listing)

    with sync_playwright() as p:
        browser = p.chromium.launch()

        for search in config.get("searches", []):
            name = search.get("name", search.get("url"))
            platform = search.get("platform")
            url = search.get("url")

            parser = PARSERS.get(platform)
            if not parser:
                print(f"Nieznana platforma '{platform}' dla '{name}' — pomijam.")
                continue

            print(f"Sprawdzam: {name} ({platform})")
            html = render_html(browser, url, wait_selector=WAIT_SELECTORS.get(platform))
            listings = parser(html) if html else []
            print(f"  Znaleziono na stronie: {len(listings)} ofert")

            seen_ids = set(seen.get(name, []))
            new_listings = [l for l in listings if l["id"] not in seen_ids]

            for listing in new_listings:
                all_new.append((name, listing))

            # Zapamiętujemy WSZYSTKIE aktualnie widziane id (nie tylko nowe),
            # żeby lista nie rosła w nieskończoność i żeby ogłoszenia usunięte
            # z wyników nie generowały fałszywych alertów po powrocie.
            # UWAGA: jeśli render_html zwróci None (błąd), listings=[] — w
            # takim wypadku NIE nadpisujemy pamięci pustą listą, żeby
            # tymczasowa awaria strony nie skasowała historii.
            if html is not None:
                seen[name] = [l["id"] for l in listings]

        browser.close()

    if len(all_new) == 1:
        # Dokładnie jedna nowa oferta — proste, pojedyncze powiadomienie.
        search_name, listing = all_new[0]
        title = f"🆕 {search_name}"
        message = f"{listing['title']}\n{listing['price']}"
        send_notification(title, message, listing["link"])

    elif len(all_new) > 1:
        # Kilka nowych ofert naraz (z jednego lub kilku wyszukiwań) —
        # jedno zbiorcze powiadomienie zamiast spamu.
        title = f"🆕 {len(all_new)} nowych ofert"
        lines = []
        for search_name, listing in all_new[:MAX_ITEMS_IN_MESSAGE]:
            lines.append(f"[{search_name}] {listing['title']} — {listing['price']}\n{listing['link']}")
        if len(all_new) > MAX_ITEMS_IN_MESSAGE:
            lines.append(f"...i {len(all_new) - MAX_ITEMS_IN_MESSAGE} więcej.")
        message = "\n\n".join(lines)
        # Klik w powiadomienie prowadzi do pierwszej oferty z listy.
        send_notification(title, message, all_new[0][1]["link"])

    save_json(SEEN_PATH, seen)
    print(f"Gotowe. Nowych ogłoszeń: {len(all_new)}.")


if __name__ == "__main__":
    sys.exit(main())
