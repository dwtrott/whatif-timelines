"""Drive the GUI headlessly and take screenshots (used during development)."""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/shots"


async def main():
    import os
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await pg.goto(BASE)
        await pg.wait_for_timeout(1500)
        # dismiss help if shown
        if await pg.is_visible("#helpModal"):
            await pg.click("#helpModal .primary")
        await pg.screenshot(path=f"{OUT}/01_overview.png")
        evs = await pg.query_selector_all(".ev")
        print("events drawn:", len(evs))
        if evs:
            await evs[min(6, len(evs) - 1)].click()
            await pg.wait_for_timeout(300)
            await pg.screenshot(path=f"{OUT}/02_event.png")
        evs = await pg.query_selector_all(".ev")
        lanes = await pg.query_selector_all(".laneLabel")
        if len(lanes) > 1:
            await lanes[1].click()
            await pg.wait_for_timeout(300)
            await pg.screenshot(path=f"{OUT}/03_branch.png")
        # fork from an event via the form
        evs = await pg.query_selector_all(".ev")
        if evs:
            await evs[2].click()
            await pg.wait_for_timeout(200)
            if await pg.query_selector("#forkPremise"):
                await pg.fill("#forkPremise", "A surprise resignation changes everything.")
                await pg.fill("#forkRounds", "3")
                await pg.click("text=Fork timeline")
                await pg.wait_for_timeout(6000)
                await pg.screenshot(path=f"{OUT}/04_forked.png")
        await pg.click("#newScenarioBtn")
        await pg.wait_for_timeout(400)
        await pg.screenshot(path=f"{OUT}/05_new.png")
        await pg.keyboard.press("Escape")
        print("JS errors:", errors)
        await b.close()


asyncio.run(main())
