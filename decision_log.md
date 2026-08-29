# Decision Log — t_2de6d68b

## Task
Napraw zepsuty workflow GitHub Actions w repo ScuraUrsa/hermes-notification-bridge.
Workflow: CI — Notification Bridge. Przyczyna (zdiagnozowana): Run unit tests failuje.

## Context
Workflow `ci.yml` ma trzy joby: `unit-tests`, `e2e-tests` (needs unit-tests),
`docker-build` (needs unit-tests + e2e-tests). Ostatnie dwa runy na master
(4aeed8c, e1b636a) kończyły się `failure` w jobie `unit-tests`.

## Diagnosis (z logów — gh run view --log-failed / API)
Run 27851951986, job unit-tests (82432500234), log:
```
python -m pytest tests/unit/ -v --timeout=30 --asyncio-mode=auto
ERROR: usage: python -m pytest [options] [file_or_dir] [...]
python -m pytest: error: unrecognized arguments: --timeout=30
##[error]Process completed with exit code 4.
```
**Root cause:** workflow przekazuje `--timeout=30` do pytest, ale nigdy nie
instaluje `pytest-timeout`. Bez tego pluginu pytest nie rozpoznaje flagi
`--timeout` i kończy się exit code 4 (błąd użycia), zanim uruchomi jakikolwiek test.

## Decisions

### D1. Dodać `pytest-timeout` do instalacji zależności w obu jobach testowych
- `unit-tests` i `e2e-tests` używają `--timeout=30`, więc oba potrzebują pluginu.
- Zmiana: `pip install pytest pytest-asyncio pytest-aiohttp aiohttp pytest-timeout`
  (dodano `pytest-timeout`) w obu jobach.
- Alternatywa rozważona: usunięcie `--timeout=30` z wywołań pytest. Odrzucona —
  timeout jest wartościową ochroną przed zawieszeniem testów w CI; lepiej
  dostarczyć plugin, który go obsługuje.

### D2. Nie zmieniać testów ani kodu źródłowego
- Lokalna weryfikacja z `pytest-timeout` zainstalowanym: `tests/unit/` → 58 passed,
  `tests/e2e/` → 5 passed. Żaden test nie wymagał poprawki.
- Uwaga: `test_push_auth_header` oczekuje `Bearer hermes-bridge-dev-key` (domyślny
  `BRIDGE_AUTH_KEY`). W CI zmienna `BRIDGE_AUTH_KEY` nie jest ustawiona, więc
  używany jest default i test przechodzi. (Lokalnie w shellu maintainera zmienna
  była ustawiona na realny token, co dawało fałszywy fail — to artefakt środowiska,
  nie błąd repo.)

## Results
- Commit `5f1d063` na master: `ci: install pytest-timeout for --timeout flag`
  (zmiana tylko w `.github/workflows/ci.yml`, 2 insertions / 2 deletions).
- Workflow run 33250340959 (head 5f1d063) → **completed success**:
  - unit-tests → success
  - e2e-tests → success
  - docker-build → success
- Weryfikacja realna: wszystkie 3 joby przeszły na GitHub Actions.

---
