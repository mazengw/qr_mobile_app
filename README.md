# QR Vault Mobile (Flet)



Professional phone-style client for the Django QR Vault APIs.



## Features



- Phone + OTP login (JWT stored locally)

- Home: owned + shared storages together

- Scan QR (type/paste value, including `share:<uuid>`)

- **Public vaults**: owner can make a storage public — any signed-in user who scans the QR can view (read-only); only owner / shared writers can edit

- Storage browser with All / Images / Docs filters

- Upload (multi-file), download (decompressed), archive, delete

- Share by phone with `read` / `write` / `manage`

- **Offline notes**: create/edit/delete notes without network; auto-sync when back online

- **Offline file browse**: snapshots + durable file cache (`Save offline` button); open cached files when offline

- Dark teal vault UI



## Run



1. Start Django API:



```bash

cd test/qr_vault

.\.venv\Scripts\activate

python manage.py migrate

python manage.py runserver

```



2. Install & launch Flet app:



```bash

cd test/qr_vault/mobile_app

python -m venv .venv

.\.venv\Scripts\activate

pip install -r requirements.txt

python main.py

```



Default API URL inside the app: `http://127.0.0.1:8000`  

Dev OTP: `123456`



## Notes



- App data uses a writable store: `FLET_APP_STORAGE_DATA` on Android/iOS, or `~/.qr_vault/` on desktop
- Downloads go to `<app_data>/downloads/`
- Session saved at `<app_data>/session.json`
- Offline queue + cache at `<app_data>/offline/` (home list, vault snapshots, file blobs, note sync queue)
- Camera QR scanning can be added later; current scan screen accepts the QR payload text (same value the mobile camera would decode)

