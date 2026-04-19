# Freight Quote Agent Context

## Current State

- Project root: `/Users/narekderavanesian/FreightQuoteAgent`
- Run command:

```bash
./.venv/bin/python run_webapp.py
```

- Local app URL:

```text
http://127.0.0.1:8001/
```

- The web server is FastAPI/Uvicorn.
- The UI submits quote runs to `/api/run-stream`.
- The server code in `webapp/server.py` was changed to use async route handlers while avoiding carrier parallelism.
- `quote_schneider.py` now uses Playwright's async API.
- The other carrier scripts still use Playwright's synchronous API.

## Git Restore Points

- `bc8dcd8` - checkpoint before async quote runner changes.
- `d5d5f8b` - current async/sequential server change.

To revert only the async/sequential server change:

```bash
git revert d5d5f8b
```

## Important Current Behavior

- `webapp/server.py` now uses `async def` for `/api/run` and `/api/run-stream`.
- Carrier jobs run one at a time through `run_jobs_sequentially(...)`.
- Blocking Google Sheets and synchronous Playwright work is currently wrapped with `asyncio.to_thread(...)`.
- A `QUOTE_RUN_LOCK` prevents overlapping quote runs.
- Schneider is true async inside the carrier script via `quote_schneider_async(...)`.
- Other carriers are async at the web request layer only and still run their synchronous Playwright work in `asyncio.to_thread(...)`.
- Because carriers are intentionally not parallel, async alone will not make the total quote run much faster.

## User Goal

The user asked whether carrier scripts can also use async code.

Goal: convert carrier automation from Playwright sync API to Playwright async API carefully, while preserving one-carrier-at-a-time execution unless the user explicitly asks to reintroduce parallelism.

Current implementation progress:

- Done: `quote_schneider.py`
- Pending: `quote_total.py`, `quote_central.py`, `quote_numark.py`, `quote_tforce.py`, `quote_mycarrier.py`, `quote_glt.py`
- `webapp/server.py` calls Schneider through `run_schneider_job_async(...)`.
- Unconverted carriers still run sequentially through `asyncio.to_thread(...)`.

## Carrier Scripts To Convert

- `quote_total.py`
- `quote_central.py`
- `quote_numark.py`
- `quote_tforce.py`
- `quote_mycarrier.py`
- `quote_schneider.py` - converted to async
- `quote_glt.py`

Current pattern:

```python
from playwright.sync_api import sync_playwright

def quote_total(data, *, headless=True, slow_mo=0):
    with sync_playwright() as p:
        ...
```

Target pattern:

```python
from playwright.async_api import async_playwright

async def quote_total_async(data, *, headless=True, slow_mo=0):
    async with async_playwright() as p:
        ...

def quote_total(data, *, headless=True, slow_mo=0):
    return asyncio.run(quote_total_async(data, headless=headless, slow_mo=slow_mo))
```

Keep sync wrappers at first so existing CLI usage and imports do not break.

## Recommended Conversion Plan

1. Start with one smaller or lower-risk carrier script.
   - Good first candidates: `quote_schneider.py` or `quote_total.py`.
   - Avoid converting all scripts at once.

2. Add an async entry point next to the existing sync function.
   - Example: `quote_total_async(...)`.
   - Keep the existing `quote_total(...)` as a wrapper for compatibility.

3. Convert Playwright imports.
   - Replace `from playwright.sync_api import sync_playwright` with `from playwright.async_api import async_playwright`.
   - If exceptions or types are imported from Playwright, convert those imports too.

4. Convert Playwright operations to `await`.
   - `page.goto(...)` -> `await page.goto(...)`
   - `page.wait_for_timeout(...)` -> `await page.wait_for_timeout(...)`
   - `locator.fill(...)` -> `await locator.fill(...)`
   - `locator.click(...)` -> `await locator.click(...)`
   - `locator.inner_text(...)` -> `await locator.inner_text(...)`
   - `locator.count()` -> `await locator.count()`
   - `locator.is_visible()` -> `await locator.is_visible()`
   - `page.screenshot(...)` -> `await page.screenshot(...)`
   - `page.content()` -> `await page.content()`

5. Convert helper functions that call Playwright.
   - Any helper using `page`, `locator`, `frame`, or `browser` methods must become `async def`.
   - All callers must `await` those helpers.

6. Keep file writes synchronous unless they become a problem.
   - Calls like `Path(...).write_text(...)` are acceptable initially.
   - Do not over-convert unrelated code.

7. Update `webapp/server.py` after one carrier has an async implementation.
   - Prefer calling the async carrier directly from the async route.
   - Keep sequential execution.
   - Do not use `asyncio.gather(...)` unless the user explicitly asks for parallelism.

8. Add timing logs before broad conversion.
   - Log start/end time per carrier.
   - Log major slow steps: login, quote form open, form fill, submit, result scrape.
   - This helps prove whether async conversion or wait reduction is improving speed.

9. Replace fixed waits where safe.
   - Many scripts use `page.wait_for_timeout(...)`.
   - Replace with targeted waits only when the expected page condition is clear.
   - Good targets: result row visible, submit button enabled, quote total present, navigation complete.

10. Verify after each carrier.
    - Run compile check:

```bash
./.venv/bin/python -m py_compile quote_total.py webapp/server.py
```

    - Run the app:

```bash
./.venv/bin/python run_webapp.py
```

    - Test one selected carrier from the UI before converting the next one.

## Risks

- Async conversion is broad because every Playwright operation needs `await`.
- A missed `await` can produce subtle runtime failures.
- Playwright sync and async APIs should not be mixed in the same execution path.
- Existing command-line behavior may break if sync wrappers are not preserved.
- Total runtime may not improve if carriers remain sequential and fixed waits remain.

## Current Untracked Files

These files existed before creating this context note and were not committed:

- `mycarrier_carrier_selection_missing.html`
- `pickup_city_not_found.png`
