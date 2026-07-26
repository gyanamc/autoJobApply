import asyncio
import random
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext
from config import config

class BrowserManager:
    def __init__(self):
        # Resolve the persistent directory path
        user_data_path = Path(config.CHROME_USER_DATA_DIR)
        user_data_path.mkdir(parents=True, exist_ok=True)
        self.user_data_dir = str(user_data_path.resolve())

    async def get_browser_context(self, playwright, headed: bool = False) -> BrowserContext:
        """
        Launches or attaches to a Chromium context.
        Uses persistent directory locally, or falls back to storage state JSON (state.json) on cloud environments.
        """
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--window-position=0,0",
            "--ignore-certificate-errors",
        ]
        
        state_file = Path(__file__).resolve().parent / "state.json"
        
        if not headed and state_file.exists():
            print(f"[Browser Manager] Loading authenticated storage state from: {state_file}")
            browser = await playwright.chromium.launch(headless=True, args=args)
            context = await browser.new_context(
                storage_state=str(state_file),
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                bypass_csp=True
            )
        else:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=not headed,
                args=args,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                bypass_csp=True,
            )
        
        # Add stealth script to page
        await context.add_init_script(
            "const newProto = Navigator.prototype; delete newProto.webdriver; Navigator.prototype = newProto;"
        )
        
        # Set default timeouts
        context.set_default_timeout(30000)
        context.set_default_navigation_timeout(30000)
        
        return context

    async def human_delay(self, min_s: float = None, max_s: float = None):
        """Sleeps for a random duration to mimic human pauses."""
        min_val = min_s if min_s is not None else config.HUMAN_DELAY_MIN
        max_val = max_s if max_s is not None else config.HUMAN_DELAY_MAX
        delay = random.uniform(min_val, max_val)
        await asyncio.sleep(delay)

    async def human_type(self, element, text: str):
        """Types text with random delays between keypresses to mimic human typing."""
        for char in text:
            await element.type(char)
            # 50ms to 150ms delay between keys
            await asyncio.sleep(random.uniform(0.05, 0.15))
        await self.human_delay(0.2, 0.5)

    async def click_with_delay(self, element):
        """Clicks an element and pauses briefly."""
        await element.click()
        await self.human_delay(1.0, 2.0)

    async def scroll_page_randomly(self, page, distance_range=(300, 700), steps=3):
        """Scrolls the page down and up randomly to mimic a user scanning details."""
        for _ in range(steps):
            direction = 1 if random.random() > 0.2 else -1
            distance = random.randint(*distance_range) * direction
            await page.evaluate(f"window.scrollBy(0, {distance})")
            await self.human_delay(0.5, 1.5)
            
browser_manager = BrowserManager()
