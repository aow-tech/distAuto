"""
@abstractofworld daily poster.

Runs once a day (GitHub Actions). It:
  1. finds today's row in schedule.csv (India time),
  2. posts that image + caption to Instagram,
  3. marks the row as posted,
  4. deletes the image from posts/ (cleanup).

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
SCHEDULE = "schedule.csv"
POSTS_DIR = "posts"


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
        sys.exit(f"Instagram API error on {path}: {e.read().decode()}")


def main():
    today = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    with open(SCHEDULE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())

    row = next((r for r in rows if r["date"] == today), None)
    if row is None:
        print(f"Nothing scheduled for {today}.")
        return
    if row["posted"]:
        print(f"{today} is already posted ({row['posted']}).")
        return

    image_path = os.path.join(POSTS_DIR, row["image"])
    if not os.path.exists(image_path):
        sys.exit(f"Image {image_path} is missing.")
    image_url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{POSTS_DIR}/{urllib.parse.quote(row['image'])}"

    print(f"Today: {today}\nImage: {image_url}\nCaption:\n{row['caption']}\n")
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
    page_token = page["access_token"]
    print(f"Posting as @{ig.get('username')} (via Page '{page['name']}')")

    container = call("POST", f"{ig['id']}/media", page_token, image_url=image_url, caption=row["caption"])["id"]

    for _ in range(30):                      # wait up to ~2.5 minutes for Instagram to fetch the image
        status = call("GET", container, page_token, fields="status_code").get("status_code")
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            sys.exit(f"Instagram could not process the image (status {status}).")
        time.sleep(5)
    else:
        sys.exit("Timed out waiting for Instagram to process the image.")

    media_id = call("POST", f"{ig['id']}/media_publish", page_token, creation_id=container)["id"]
    print(f"Posted. Media id {media_id}")

    # Mark as posted and clean up the image.
    row["posted"] = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M")
    row["media_id"] = media_id
    with open(SCHEDULE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)
    os.remove(image_path)
    print(f"Removed {image_path}")


if __name__ == "__main__":
    main()
