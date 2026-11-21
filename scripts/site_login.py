"""Open a dedicated browser profile for a user-requested video site sign-in."""
import argparse
from pathlib import Path
import subprocess
import sys

from common import APP_HOME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("youtube", "tiktok"), required=True)
    platform = parser.parse_args().platform
    profile = APP_HOME / "login-profiles" / platform
    profile.mkdir(parents=True, exist_ok=True)
    url = "https://www.youtube.com" if platform == "youtube" else "https://www.tiktok.com/login"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                print("Installing Chromium for sign-in", flush=True)
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                               check=True, stdout=sys.stdout, stderr=subprocess.STDOUT)
            context = playwright.chromium.launch_persistent_context(str(profile), headless=False)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            context.wait_for_event("close")
    except Exception as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
