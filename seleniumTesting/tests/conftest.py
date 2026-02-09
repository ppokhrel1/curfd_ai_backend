from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException, WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.remote.webdriver import WebDriver


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _as_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_chromium_binary() -> str | None:
    explicit = (
        os.getenv("CHROME_BINARY", "").strip()
        or os.getenv("CHROMIUM_BINARY", "").strip()
    )
    if explicit:
        return explicit

    for candidate in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "chrome",
    ):
        found = shutil.which(candidate)
        if found:
            return found
    return None


@pytest.fixture(scope="session")
def api_base_url() -> str:
    return os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")


@pytest.fixture(scope="session")
def test_email() -> str:
    value = os.getenv("TEST_EMAIL", "").strip()
    if not value:
        pytest.fail("TEST_EMAIL is not set. Add it in seleniumTesting/.env")
    return value


@pytest.fixture(scope="session")
def test_password() -> str:
    value = os.getenv("TEST_PASSWORD", "").strip()
    if not value:
        pytest.fail("TEST_PASSWORD is not set. Add it in seleniumTesting/.env")
    return value


@pytest.fixture(scope="session")
def harness_url() -> str:
    return (ROOT / "harness" / "index.html").resolve().as_uri()


@pytest.fixture(scope="session")
def enable_runpod_tests() -> bool:
    return _as_bool(os.getenv("ENABLE_RUNPOD_TESTS"), default=False)


@pytest.fixture(scope="session")
def driver() -> WebDriver:
    browser = os.getenv("BROWSER", "chromium").strip().lower()
    if browser not in {"chrome", "chromium"}:
        pytest.fail("BROWSER must be one of: chrome, chromium")

    remote_url = os.getenv("SELENIUM_REMOTE_URL", "").strip()
    headless = _as_bool(os.getenv("HEADLESS"), default=True)

    driver_path = os.getenv("CHROMEDRIVER_PATH", "").strip()
    binary = _resolve_chromium_binary()

    tmp_profile_dir = tempfile.mkdtemp(prefix="selenium-chrome-profile-")
    tmp_data_path = tempfile.mkdtemp(prefix="selenium-chrome-data-")
    tmp_cache_dir = tempfile.mkdtemp(prefix="selenium-chrome-cache-")

    def _build_options(use_new_headless: bool) -> ChromeOptions:
        options = ChromeOptions()
        if binary:
            options.binary_location = binary

        if headless:
            options.add_argument("--headless=new" if use_new_headless else "--headless")

        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-zygote")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--remote-debugging-pipe")
        options.add_argument(f"--user-data-dir={tmp_profile_dir}")
        options.add_argument(f"--data-path={tmp_data_path}")
        options.add_argument(f"--disk-cache-dir={tmp_cache_dir}")
        return options

    def _start_driver(use_new_headless: bool) -> WebDriver:
        options = _build_options(use_new_headless)
        if remote_url:
            return webdriver.Remote(command_executor=remote_url, options=options)
        service = ChromeService(executable_path=driver_path) if driver_path else ChromeService()
        return webdriver.Chrome(service=service, options=options)

    try:
        try:
            drv = _start_driver(use_new_headless=True)
        except (SessionNotCreatedException, WebDriverException):
            # Chromium/driver combos sometimes fail on --headless=new.
            drv = _start_driver(use_new_headless=False)

        drv.implicitly_wait(1)
        yield drv
        drv.quit()
    finally:
        shutil.rmtree(tmp_profile_dir, ignore_errors=True)
        shutil.rmtree(tmp_data_path, ignore_errors=True)
        shutil.rmtree(tmp_cache_dir, ignore_errors=True)
