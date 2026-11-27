# Roadmap integracji i wysyłki dużych plików

Celem jest jeden PR do `beta` z jednym końcowym commitem. Najpierw integrujemy `upstream/master` z funkcjami forka i domykamy poprawki bezpieczeństwa. Następnie dodajemy dwa tryby pracy bota ze wspólnym poleceniem startowym. Na końcu rozbudowujemy testy automatyczne i dodajemy CI/CD.

## Etap 1 — integracja upstream

1. **Punkt odniesienia.** Utworzyć gałąź z `beta` w osobnym worktree. Przygotować testy używające przykładowych odpowiedzi serwisów i atrapy Telegrama. Zapisać oczekiwane zachowanie routingu, konfiguracji, wysyłki mediów oraz obsługi wątków.
2. **Scalenie `upstream/master`.** Zachować historię obu stron. Rozwiązać konflikty w `bot.py` i handlerze YouTube. Przyjąć upstreamowy rejestr handlerów, `PostData` i wspólną warstwę wysyłki. Połączyć normalizację URL YouTube z dotychczasowym limitem pobierania 123 MiB, limitem 20 minut i obsługą brakujących danych autora.
3. **Funkcje forka.** Przenieść kolejno Mastodona z obsługą Misskey i wątków, Streamable oraz Bluesky. Dla każdego serwisu sprawdzić routing, pobieranie i wysyłkę. Zachować zgodność dotychczasowego formatu konfiguracji, szablon i pliki developerskie.
4. **9GAG i Booru.** Zachować poprawki regexów z `0481a15` i `df96c80`. Dodać walidację schematu, hosta i celu przekierowań przed żądaniem, także dla URL-i mediów przekazywanych do wspólnego downloadera. W Booru sprawdzić parsowanie hosta i identyfikatora oraz dodać timeout. W 9GAG zapewnić timeouty i zamknięcie Firefoksa po błędzie. Przetestować te ścieżki na atrapach bez żądań do rzeczywistych usług.
5. **Gotowość do merge’a.** Sprawdzić pojedyncze media, albumy, cytaty, wątki, błędy pobrania i sprzątanie plików. Wykonać próbę na Windowsie z osobnym botem testowym. Opisać w PR zmiany konfiguracji i procedurę powrotu.

`upstream/develop` zawiera dodatkowe poprawki 9GAG i downloadera oraz wysyłkę wideo bezpośrednio z URL. W etapie 4 wykorzystać sprawdzone zmiany timeoutów 9GAG, jeśli pasują do walidacji przekierowań. Pozostałe zmiany ocenić osobno. Handler WIP z `upstream/reddit` jest kandydatem na późniejsze rozszerzenie.

## Etap 2 — dwa tryby pracy i jeden start

| Tryb | Co uruchamia wspólne polecenie | Wysyłka plików |
| --- | --- | --- |
| `public` (domyślny) | Proces bota połączony z publicznym Bot API Telegrama | Do 50 MB dla typowych uploadów wideo i dokumentów, z uwzględnieniem limitów danej metody |
| `local` | Serwer `telegram-bot-api --local`, a po potwierdzeniu gotowości proces bota | Do 2000 MB, z uwzględnieniem limitów metody i konfiguracji wdrożenia |

[Dokumentacja limitów Telegrama](https://core.telegram.org/bots/features#local-bot-api)

1. **Próba techniczna.** Budować natywny serwer z przypiętej rewizji oficjalnego źródła i dołączać go do paczki dla Windows, Linux i macOS. Na Windowsie, jako pierwszym środowisku docelowym, potwierdzić polling i wysłać plik większy niż 50 MB na koncie testowym. Zweryfikować wysyłkę ze ścieżki lokalnej przy wspólnym katalogu, również ze spacjami w ścieżce. [Instrukcje serwera Telegram Bot API](https://github.com/tdlib/telegram-bot-api#installation)
2. **Konfiguracja i start.** Wprowadzić wybór trybu korzystający domyślnie z `public`. Wspólny launcher w Pythonie ma wykrywać dołączoną binarkę, startować oba procesy jednym poleceniem w trybie lokalnym i przechowywać `api_id`, `api_hash` oraz dane poza paczką.
3. **Cykl życia procesów.** W trybie lokalnym launcher sprawdza konfigurację, uruchamia serwer na interfejsie lokalnym i czeka na gotowość z limitem czasu. Następnie uruchamia dokładnie jedną instancję bota. Awaria startu kończy pracę z czytelnym błędem. Przy zamknięciu launcher zatrzymuje bota, a potem uruchomiony przez siebie serwer. Przetestować zajęty port, błąd startu serwera i Ctrl+C na trzech systemach.
4. **Polityka rozmiaru.** Ustalić limit wysyłki według trybu i metody Telegrama, a limit pobierania oraz czas trwania traktować jako osobne ustawienia. Sprawdzić szacowany rozmiar przed pobraniem i faktyczny rozmiar przed wysłaniem. Objąć limitem również pliki o nieznanym rozmiarze w trakcie pobierania. Wspólna warstwa wysyłki ma obsługiwać oba tryby dla wszystkich handlerów.
5. **Gotowość do merge’a.** Potwierdzić start, polling i wysyłkę w obu trybach z tokenem testowym. W trybie lokalnym sprawdzić plik powyżej 50 MB, album, odmowę ponad ustawionym limitem, przerwane pobranie i timeout wysyłki. Sprawdzić zużycie dysku i pamięci oraz sprzątanie plików. Przygotować instrukcję przełączenia produkcji i powrotu do trybu publicznego.

Migracja istniejącego bota do lokalnego API wymaga jednorazowego `logOut` z publicznego API po zatrzymaniu dotychczasowej instancji. To osobny krok wdrożenia. Telegram blokuje ponowne zalogowanie do publicznego API przez 10 minut od udanego `logOut`, co trzeba uwzględnić przy powrocie. [Dokumentacja `logOut`](https://core.telegram.org/bots/api#logout)

Nowe serwisy po integracji korzystają z rejestru handlerów i wspólnego `PostData`. Obsługa dużych plików pozostaje we wspólnym downloaderze i warstwie wysyłki, dzięki czemu nowy handler od razu obsługuje oba tryby.

## Publikacja na GitHubie

Na lokalnej gałęzi integracyjnej robimy małe commity dla kolejnych zmian. Po przygotowaniu zmian ze wszystkich etapów tworzymy osobną gałąź publikacyjną z aktualnej `origin/beta` i składamy na niej zmiany przez `git merge --squash`. Sprawdzamy końcowy diff, ponawiamy testy i weryfikujemy, że gałąź ma dokładnie jeden commit ponad `origin/beta`. Na GitHub trafia wyłącznie gałąź publikacyjna, z jednym PR-em do `beta`. Po przejściu CI PR scalamy przez Squash and merge albo fast-forward, jeśli jest dostępny, żeby `beta` również dostała jeden commit.

Historia robocza, w tym merge z upstreamem, zostaje na lokalnej gałęzi integracyjnej. Commit publikacyjny zawiera wynik tego scalenia bez relacji do gałęzi upstream. W opisie commita lub PR zapisujemy SHA użytego `upstream/master`, żeby ułatwić kolejną synchronizację.

## Etap 3 — testy automatyczne i CI/CD

1. **Testy automatyczne.** Rozbudować testy regresji przygotowane podczas integracji. Użyć przykładowych odpowiedzi serwisów i atrap Telegrama, downloadera oraz przeglądarki. Objąć routing 9GAG i Booru, walidację przekierowań, limity YouTube, konfigurację, albumy i wątki oraz sprzątanie po błędach. Dla launchera sprawdzić wybór trybu, gotowość serwera, awarię startu i kolejność zamykania procesów.
2. **CI w GitHub Actions.** Na PR-ach do `beta` i po zmianach na `beta` instalować zależności w czystym środowisku z Pythonem 3.10, uruchamiać `pip check`, kompilację źródeł, sprawdzenie importów bibliotek i testy offline. Zacząć od Windowsa i Linuksa, a testy launchera uruchamiać również na macOS. CI ma działać bez tokena Telegrama i bez dostępu do produkcji. Próby pollingu i wysyłki wykonywać osobno z tokenem bota testowego.
3. **Paczka po udanym CI.** Na `beta` przygotowywać osobne paczki wdrożeniowe powiązane z dokładnym SHA dla Windows, Linux i macOS. Każda paczka zawiera kod, launcher, śledzony szablon konfiguracji i właściwą binarkę Bot API. Kontekst Dockera dopuszcza pliki potrzebne do uruchomienia, w tym binarkę z linuksowej paczki.
4. **Ręcznie uruchamiane wdrożenie na Windowsie.** Najpierw ustalić, jak uruchamiana jest obecna instancja. Następnie przygotować wdrożenie konkretnej paczki z zachowaniem konfiguracji i danych, kontrolowanym restartem, sprawdzeniem działania i powrotem do poprzedniej wersji w przypadku błędu. Przećwiczyć wdrożenie i powrót na instancji testowej przed użyciem na produkcji.

Etap jest gotowy, gdy ten sam zestaw testów przechodzi lokalnie i w CI, paczkę można powiązać z konkretnym commitem, a próbne wdrożenie i powrót do poprzedniej wersji działają na Windowsie.

## Wydanie i przeniesienie na serwer

1. Poczekać na testy `beta`, a przed scaleniem sprawdzić różnice względem własnego `master`. Po zaakceptowaniu zmian scalić `beta` do `master` i oznaczyć zweryfikowany commit tagiem wersji `v*`. Zwykłe pushe i PR-y do obu gałęzi uruchamiają testy. Tag uruchamia testy przed kompilacją i publikacją paczek oraz artefaktów Dockera.
2. Przygotować dedyka z paczką odpowiednią dla systemu i architektury. Zachować konfigurację i potrzebne sesje w osobnym katalogu, przenosząc je prywatnym kanałem. Zweryfikować start na osobnym tokenie testowym, jeśli taki jest dostępny.
3. Ustalić sposób zatrzymania starej instancji oraz jej tryb API. Przy przenoszeniu tego samego tokena zatrzymać dotychczasowy polling przed startem nowego. Zmianę trybu API lub hosta lokalnego API wykonać według procedury migracji. Potwierdzić polling oraz wysłanie pojedynczego pliku i albumu. W trybie lokalnym sprawdzić także plik większy niż 50 MB. Zachować poprzednią wersję i kopię danych do powrotu. [Instrukcje migracji serwera Bot API](https://github.com/tdlib/telegram-bot-api#moving-a-bot-from-one-local-server-to-another)

Dalszy rozwój prowadzimy w forku, przenosząc wybrane aktualizacje z upstreamu lub przepisując konkretne funkcje. Pełna synchronizacja upstreamu może być osobną decyzją dla przyszłego wydania.
