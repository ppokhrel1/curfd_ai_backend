# Selenium Testing for CURFD Backend

This project provides browser-based Selenium tests for core backend flows:

- login
- create session
- create chat
- list chats by session id

It uses a small local test page (`harness/index.html`) that calls your API via `fetch`.
The main test now covers documented activities from:

- `Documention.md`
- `app/Documentation_chat.md`

## 1) Setup

```bash
cd seleniumTesting
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

- `API_BASE_URL` (example: `http://localhost:8000/api/v1`)
- `TEST_EMAIL`
- `TEST_PASSWORD`
- `ENABLE_RUNPOD_TESTS` (`true`/`false`, default `false`)
- `BROWSER` (`chromium` or `chrome`, default `chromium`)
- `CHROMIUM_BINARY` (optional explicit browser path)
- `CHROMEDRIVER_PATH` (optional explicit chromedriver path)

## 2) Run tests

```bash
pytest -q
```

## 3) Notes

- Backend must be running before tests.
- For Supabase auth, make sure your backend auth config is valid.
- Default runs in headless Chromium/Chrome.
- You can use remote Selenium Grid with `SELENIUM_REMOTE_URL`.
- Runpod and chat-socket checks are optional and controlled by `ENABLE_RUNPOD_TESTS`.
