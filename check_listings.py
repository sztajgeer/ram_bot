"""
Sprawdza podane wyszukiwania na OLX / Allegro / Allegro Lokalnie / Vinted
i wysyła powiadomienie push na telefon (przez ntfy.sh) gdy pojawią się nowe
ogłoszenia. Jeśli w jednym uruchomieniu pojawi się kilka nowych ofert naraz,
są grupowane w jedno zbiorcze powiadomienie zamiast spamu.

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

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
SEEN_PATH = ROOT / "data" / "seen.json"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}" if NTFY_TOPIC else None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}


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
# Parsery dla poszczególnych platform.
# UWAGA: struktura HTML tych serwisów zmienia się co jakiś czas — jeśli bot
# przestanie znajdować ogłoszenia, prawdopodobnie trzeba będzie zaktualizować
# selektory poniżej (sprawdź "Narzędzia deweloperskie" w przeglądarce).
# ---------------------------------------------------------------------------

def fetch_olx(url):
    items = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[OLX] błąd pobierania {url}: {e}")
        return items

    soup = BeautifulSoup(resp.text, "lxml")
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


def fetch_allegro(url):
    items = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[Allegro] błąd pobierania {url}: {e}")
        return items

    soup = BeautifulSoup(resp.text, "lxml")
    # Allegro renderuje sporo po stronie klienta i ma ochronę antybotową —
    # ten selektor może wymagać dostrojenia albo może czasem zwracać 0 wyników
    # nawet gdy oferty istnieją (wtedy warto rozważyć oficjalne REST API Allegro).
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


def fetch_vinted(url):
    """
    Vinted ma silną ochronę antybotową (Datadome) na zwykłym HTML, dlatego
    korzystamy z ich wewnętrznego (nieoficjalnego) endpointu JSON, który
    używany jest przez samą stronę. To rozwiązanie może przestać działać,
    jeśli Vinted zmieni zabezpieczenia — w takim wypadku trzeba będzie
    poszukać aktualnego sposobu (np. inny endpoint albo pobieranie cookies).
    """
    items = []
    try:
        parsed_query = url.split("?", 1)[1] if "?" in url else ""
        api_url = f"https://www.vinted.pl/api/v2/catalog/items?{parsed_query}&per_page=20&order=newest_first"
        session = requests.Session()
        # Najpierw wejście na stronę, żeby dostać ciasteczka sesyjne.
        session.get("https://www.vinted.pl/", headers=HEADERS, timeout=20)
        resp = session.get(api_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"[Vinted] błąd pobierania {url}: {e}")
        return items

    for item in data.get("items", []):
        link = item.get("url", "")
        items.append({
            "id": str(item.get("id", link)),
            "title": item.get("title", "(brak tytułu)"),
            "price": (item.get("price") or {}).get("amount", ""),
            "link": link,
        })
    return items


def fetch_allegro_lokalnie(url):
    items = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[Allegro Lokalnie] błąd pobierania {url}: {e}")
        return items

    soup = BeautifulSoup(resp.text, "lxml")
    # Podobnie jak Allegro — selektor to najlepsza dostępna aproksymacja,
    # może wymagać dostrojenia jeśli serwis zmieni markup.
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


FETCHERS = {
    "olx": fetch_olx,
    "allegro": fetch_allegro,
    "allegro_lokalnie": fetch_allegro_lokalnie,
    "vinted": fetch_vinted,
}


MAX_ITEMS_IN_MESSAGE = 15  # żeby powiadomienie nie zrobiło się absurdalnie długie


def main():
    config = load_json(CONFIG_PATH, {"searches": []})
    seen = load_json(SEEN_PATH, {})

    all_new = []  # lista (search_name, listing)

    for search in config.get("searches", []):
        name = search.get("name", search.get("url"))
        platform = search.get("platform")
        url = search.get("url")

        fetcher = FETCHERS.get(platform)
        if not fetcher:
            print(f"Nieznana platforma '{platform}' dla '{name}' — pomijam.")
            continue

        print(f"Sprawdzam: {name} ({platform})")
        listings = fetcher(url)
        seen_ids = set(seen.get(name, []))
        new_listings = [l for l in listings if l["id"] not in seen_ids]

        for listing in new_listings:
            all_new.append((name, listing))

        # Zapamiętujemy WSZYSTKIE aktualnie widziane id (nie tylko nowe),
        # żeby lista nie rosła w nieskończoność i żeby ogłoszenia usunięte
        # z wyników nie generowały fałszywych alertów po powrocie.
        seen[name] = [l["id"] for l in listings]

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
