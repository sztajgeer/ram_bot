# Bot powiadomień OLX / Allegro / Allegro Lokalnie / Vinted → telefon (ntfy.sh)

Sprawdza wskazane wyszukiwania co 10 minut i wysyła nowe ogłoszenia na Twój
telefon jako powiadomienie push przez [ntfy.sh](https://ntfy.sh) (darmowa,
open-source'owa usługa — bez zakładania konta). Jeśli w jednym sprawdzeniu
pojawi się kilka nowych ofert naraz, bot wysyła **jedno** zbiorcze
powiadomienie zamiast spamu.

## Konfiguracja (jednorazowo)

1. **Zainstaluj apkę ntfy** na telefonie (Android: Google Play / F-Droid,
   iOS: App Store) i w niej dodaj subskrypcję na dowolnie wymyśloną,
   trudną do odgadnięcia nazwę tematu, np. `oferty-xyz123abc`
   (temat jest publiczny — każdy, kto zna nazwę, mógłby tam pisać/czytać,
   więc nie używaj oczywistej nazwy).

2. **Stwórz nowe repozytorium na GitHubie** (może być prywatne) i wrzuć do
   niego wszystkie pliki z tego folderu.

3. **Dodaj sekret w repo**: Settings → Secrets and variables → Actions →
   *New repository secret* → nazwa `NTFY_TOPIC`, wartość: nazwa Twojego
   tematu (np. `oferty-xyz123abc`, bez `https://ntfy.sh/`).

4. `config.json` jest już wypełniony Twoimi czterema wyszukiwaniami (RAM
   uszkodzona na Allegro, Allegro Lokalnie, Vinted i OLX) — nie musisz nic
   zmieniać, chyba że chcesz dodać/usunąć/zmienić jakieś wyszukiwanie:

   - `platform`: `olx`, `allegro`, `allegro_lokalnie` lub `vinted`
   - `url`: dokładny link do wyników wyszukiwania (z filtrami, jeśli chcesz)
   - `name`: dowolna nazwa, po niej bot rozróżnia oddzielne "pamięci" ogłoszeń

5. Zacommituj repo (jeśli coś zmieniałeś w `config.json`, też to zacommituj)
   — od tego momentu GitHub Actions będzie sam odpalał `check_listings.py`
   co 10 minut (patrz zakładka *Actions* w repo).

## Ważne ograniczenia

- **To jest scraping, nie oficjalne API** — serwisy mogą zmieniać HTML, co
  czasem wymaga poprawek w selektorach w `check_listings.py`.
- **Vinted, Allegro i Allegro Lokalnie** mają zabezpieczenia antybotowe.
  Jeśli bot przestanie znajdować wyniki dla którejś z tych platform, to
  najczęstsza przyczyna — daj znać, pomogę doszlifować selektory.
- Sprawdź regulaminy serwisów dot. automatycznego pobierania danych —
  używaj tego do prywatnego monitoringu, nie do niczego komercyjnego/masowego.
- Możesz odpalić sprawdzenie ręcznie: zakładka *Actions* → *Sprawdź nowe
  ogłoszenia* → *Run workflow*, żeby przetestować bez czekania na harmonogram.

## Test lokalny (opcjonalnie)

```bash
pip install -r requirements.txt
export NTFY_TOPIC="oferty-xyz123abc"
python check_listings.py
```
