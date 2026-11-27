"""
@abstractofworld auto-poster.

Each run (GitHub Actions):
  1. works out which slot it is for: "morning", "evening" or "theme",
     from the schedule that started it (or, for manual runs, the time of day),
  2. finds that row in schedule.csv (India time) and posts it to Instagram:
     one image, or a carousel when the row lists several images separated by "|",
  3. marks the row as posted,
  4. deletes the posted images from posts/ (cleanup).

Manual runs catch up first: they post the oldest missed post, if any.

Needs one secret: IG_ACCESS_TOKEN, a long-lived Facebook user access token
(from Graph API Explorer) for the Facebook Page that is linked to the Instagram account.
Set DRY_RUN=true to see what would be posted without posting.
"""
import csv
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

API = "https://graph.facebook.com/" + os.environ.get("IG_API_VERSION", "v23.0")
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")      # e.g. yourname/distAuto
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
CATCH_UP = os.environ.get("CATCH_UP", "false").lower() == "true"
CRON = os.environ.get("CRON", "")                   # the schedule that started this run, if any
SCHEDULE = "schedule.csv"
POSTS_DIR = "posts"
IST = ZoneInfo("Asia/Kolkata")

# Which slot each scheduled run is for. Must match the crons in .github/workflows/daily-post.yml.
CRON_SLOTS = {
    "10 1 * * *": "morning", "0 2 * * *": "morning",
    "40 13 * * *": "evening", "45 14 * * *": "evening",
    "10 17 * * *": "theme", "15 18 * * *": "theme",
}
ORDER = {"morning": 0, "evening": 1, "theme": 2}


class ApiError(Exception):
    pass


def call(method, path, token=None, **params):
    params["access_token"] = token or TOKEN
    data = urllib.parse.urlencode(params).encode()
    url = f"{API}/{path}"
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise ApiError(f"Instagram API error on {path}: {e.read().decode()}")


def current_slot(now):
    """Return (date, slot) this run is responsible for."""
    slot = CRON_SLOTS.get(CRON)
    if slot is None:                                   # manual run: go by the clock
        slot = "morning" if now.hour < 13 else "evening" if now.hour < 22 else "theme"
    day = now.date()
    if slot == "theme" and now.hour < 12:              # a late theme run that slipped past midnight
        day -= datetime.timedelta(days=1)
    return day.isoformat(), slot


def wait_until_ready(container, token):
    for _ in range(30):                                # up to ~2.5 minutes
        status = call("GET", container, token, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            sys.exit(f"Instagram could not process the media (status {status}).")
        time.sleep(5)
    sys.exit("Timed out waiting for Instagram to process the media.")


def norm(text):
    return " ".join((text or "").split())


def already_on_instagram(ig_id, token, caption):
    """Return the media id if our account already has a post with this exact caption."""
    recent = call("GET", f"{ig_id}/media", token, fields="id,caption,timestamp", limit=30).get("data", [])
    want = norm(caption)
    return next((m["id"] for m in recent if norm(m.get("caption")) == want), None)


def record(rows, fields, row, paths, media_id):
    row["posted"] = datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M")
    row["media_id"] = media_id
    with open(SCHEDULE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)
    for p in paths:
        if os.path.exists(p):
            os.remove(p)
            print(f"Removed {p}")


def main():
    try:
        run()
    except ApiError as e:
        sys.exit(str(e))


def run():
    now = datetime.datetime.now(IST)
    today, slot = current_slot(now)
    with open(SCHEDULE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())

    row = None
    if CATCH_UP:
        due = [r for r in rows if not r["posted"]
               and (r["date"], ORDER[r["slot"]]) < (today, ORDER[slot])]
        if due:
            row = min(due, key=lambda r: (r["date"], ORDER[r["slot"]]))
            print(f"Catching up on missed post: {row['date']} {row['slot']}.")
    if row is None:
        row = next((r for r in rows if r["date"] == today and r["slot"] == slot), None)
    if row is None:
        print(f"Nothing scheduled for {today} {slot}.")
        return
    if row["posted"]:
        print(f"{row['date']} {row['slot']} is already posted ({row['posted']}).")
        return

    images = row["image"].split("|")
    paths = [os.path.join(POSTS_DIR, name) for name in images]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        sys.exit(f"Missing images: {', '.join(missing)}")
    urls = [f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{POSTS_DIR}/{urllib.parse.quote(n)}" for n in images]

    kind = "carousel" if len(urls) > 1 else "image"
    print(f"Now: {now:%Y-%m-%d %H:%M}  Posting: {row['date']} {row['slot']} ({kind})")
    print("Images:\n  " + "\n  ".join(urls) + f"\nCaption:\n{row['caption']}\n")
    if DRY_RUN:
        print("DRY RUN: nothing was posted.")
        return
    if not TOKEN:
        sys.exit("IG_ACCESS_TOKEN secret is not set.")

    # Find the Facebook Page that is linked to the Instagram account, and its Page token.
    pages = call("GET", "me/accounts", fields="name,access_token,instagram_business_account{id,username}").get("data", [])
    page = next((p for p in pages if p.get("instagram_business_account")), None)
    if page is None:
        sys.exit("No Facebook Page with a linked Instagram account was found for this token.")
    ig = page["instagram_business_account"]
    token = page["access_token"]
    print(f"Posting as @{ig.get('username')} (via Page '{page['name']}')")

    # Never post the same thing twice: if it's already on Instagram, just record it.
    existing = already_on_instagram(ig["id"], token, row["caption"])
    if existing:
        print(f"Already on Instagram (media id {existing}). Recording it instead of posting again.")
        record(rows, fields, row, paths, existing)
        return

    if len(urls) == 1:
        container = call("POST", f"{ig['id']}/media", token, image_url=urls[0], caption=row["caption"])["id"]
    else:
        children = []
        for url in urls:
            child = call("POST", f"{ig['id']}/media", token, image_url=url, is_carousel_item="true")["id"]
            wait_until_ready(child, token)
            children.append(child)
        container = call("POST", f"{ig['id']}/media", token, media_type="CAROUSEL",
                         children=",".join(children), caption=row["caption"])["id"]
    wait_until_ready(container, token)

    try:
        media_id = call("POST", f"{ig['id']}/media_publish", token, creation_id=container)["id"]
    except ApiError as e:
        print(f"Publish reported an error: {e}\nChecking whether it went live anyway...")
        time.sleep(30)
        media_id = already_on_instagram(ig["id"], token, row["caption"])
        if not media_id:
            raise
    print(f"Posted. Media id {media_id}")
    record(rows, fields, row, paths, media_id)


if __name__ == "__main__":
    main()
