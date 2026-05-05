import time

from gspread.exceptions import APIError


def with_gsheets_retry(fn, *, attempts=5, base_delay=1.0):
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except APIError as exc:
            last_exc = exc
            status_code = None
            try:
                status_code = exc.response.status_code
            except:
                pass

            if status_code not in {429, 500, 502, 503, 504}:
                raise

            if attempt == attempts - 1:
                raise

            if status_code == 429:
                time.sleep(max(15.0, base_delay * (attempt + 1)))
            else:
                time.sleep(base_delay * (attempt + 1))

    if last_exc:
        raise last_exc
