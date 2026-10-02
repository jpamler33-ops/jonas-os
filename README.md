# X → Telegram Video Collector

Serverloser Collector über GitHub Actions. Alle 5 Minuten werden konfigurierte X-Accounts geprüft. Neue Videos werden heruntergeladen, an Telegram geschickt und per kompakter Tweet-ID-Liste dedupliziert.

## Einrichtung

1. Unter **Settings → Secrets and variables → Actions → Secrets** anlegen:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - optional/recommended: `X_COOKIES_B64`

2. X-Accounts entweder in `config/accounts.txt` eintragen oder als Repository Variable `X_ACCOUNTS` setzen, z. B.:
   `NASA,SpaceX`

3. Unter **Actions → X videos to Telegram → Run workflow** einmal manuell starten.

Der erste erfolgreiche Lauf ist standardmäßig nur Bootstrap: vorhandene Videos werden als gesehen markiert, aber nicht an Telegram gespammt. Danach werden nur neue Videos geschickt.

## X-Cookies

Für stabileren X-Zugriff eigene X-Cookies im Netscape-`cookies.txt`-Format exportieren und Base64-kodiert als Secret `X_COOKIES_B64` speichern. Token/Cookies niemals committen.

## Hinweise

- Workflow: alle 5 Minuten.
- Downloads werden nur temporär verarbeitet und nicht im Repo gespeichert.
- `state/seen.json` enthält nur verarbeitete öffentliche Tweet-IDs.
- Der Bot ist vollständig getrennt von BIGGJ.
- Nur Inhalte herunterladen/archivieren, die du verwenden darfst.
