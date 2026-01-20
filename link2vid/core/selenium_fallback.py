"""Selenium-based fallback helpers."""

from __future__ import annotations

from typing import Callable
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

LogFn = Callable[[str], None]


def selenium_fetch_m3u8(page_url: str, username: str, password: str, log: LogFn | None = None) -> str | None:
    logger = log or (lambda _msg: None)
    logger(f"[DEBUG] Starting Selenium fallback for {page_url}")
    try:
        driver = webdriver.Chrome()  # Assumes chromedriver is in PATH
        driver.get("https://gdcvault.com/login")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email")))
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "password")))
        driver.find_element(By.NAME, "email").send_keys(username)
        driver.find_element(By.NAME, "password").send_keys(password)
        driver.find_element(By.CSS_SELECTOR, "input[type=\"submit\"][value=\"LOGIN\"]").click()
        time.sleep(5)
        driver.get(page_url)
        time.sleep(3)
        html = driver.page_source
        driver.quit()
        match = __import__("re").search(r"https?://[^\"']+\.m3u8", html)
        if match:
            logger(f"[DEBUG] Found .m3u8: {match.group(0)}")
            return match.group(0)
        logger("[DEBUG] No .m3u8 found in Selenium fallback.")
        return None
    except Exception as exc:
        logger(f"[DEBUG] Selenium fallback error: {exc}")
        return None
