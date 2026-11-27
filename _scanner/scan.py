"""
Comment-queue scanner for @abstractofworld.

Looks up recent public posts from the accounts in accounts.csv (Instagram Business Discovery),
plus comments on our own posts that haven't been replied to, and writes:
  OUT/latest.json   everything, for Claude to read and write comment ideas from
  OUT/latest.md     the same, readable on GitHub
  OUT/accounts.md   which accounts could be looked up (and their follower counts)

It only reads. It never posts, comments, follows or likes anything.
Needs IG_ACCESS_TOKEN with instagram_basic, instagram_manage_insights, pages_read_engagement, pages_show_list.
Usage: python scan.py OUTDIR
"""
import csv
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://graph.facebook.com/" + os.environ.get("IG_API_VERSION", "v23.0")
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
FRESH_HOURS = 36          # how far back counts as "new"
POSTS_PER_ACCOUNT = 4     # newest posts checked per account
UTC = datetime.timezone.utc


class ApiError(Exception):
    pass


def get(path, token=None, **params):
    params["access_token"] = token or TOKEN
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get("error", {})
            raise ApiError(err.get("error_user_msg") or err.get("message") or str(e))
        except ValueError:
            raise ApiError(str(e))


def parse_ts(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def main(out):
    if not TOKEN:
        sys.exit("IG_ACCESS_TOKEN secret is not set.")
    os.makedirs(out, exist_ok=True)
    now = datetime.datetime.now(UTC)

    pages = get("me/accounts", fields="name,access_token,instagram_business_account{id,username}").get("data", [])
    page = next((p for p in pages if p.get("instagram_business_account")), None)
    if page is None:
        sys.exit("No Facebook Page with a linked Instagram account was found for this token.")
    me = page["instagram_business_account"]
    ptoken = page["access_token"]

    with open("accounts.csv", newline="", encoding="utf-8") as f:
        targets = list(csv.DictReader(f))

    media_fields = "id,caption,permalink,timestamp,media_type,media_url,thumbnail_url,like_count,comments_count"
    accounts, posts = [], []
    for t in targets:
        h = t["handle"].strip().lstrip("@")
        fields = (f"business_discovery.username({h})"
                  f"{{username,name,biography,followers_count,media.limit({POSTS_PER_ACCOUNT}){{{media_fields}}}}}")
        try:
            bd = get(me["id"], ptoken, fields=fields)["business_discovery"]
        except ApiError as e:
            accounts.append({"handle": h, "group": t["group"], "ok": False, "error": str(e)[:160]})
            print(f"  ✗ @{h}: {e}")
            continue
        accounts.append({"handle": h, "group": t["group"], "ok": True, "name": bd.get("name", ""),
                         "followers": bd.get("followers_count"), "bio": (bd.get("biography") or "")[:160]})
        print(f"  ✓ @{h}: {bd.get('followers_count')} followers")
        for m in bd.get("media", {}).get("data", []):
            ts = parse_ts(m["timestamp"])
            age = (now - ts).total_seconds() / 3600
            if age > FRESH_HOURS:
                continue
            posts.append({
                "account": h, "group": t["group"], "followers": bd.get("followers_count"),
                "permalink": m.get("permalink"), "type": m.get("media_type"),
                "age_hours": round(age, 1), "posted_utc": m["timestamp"],
                "likes": m.get("like_count"), "comments": m.get("comments_count"),
                "caption": (m.get("caption") or "")[:600],
                "image": m.get("thumbnail_url") or m.get("media_url"),
            })

    # Freshest first; among equally fresh, fewer comments = more chance to be seen.
    posts.sort(key=lambda p: (p["age_hours"] // 6, p["comments"] or 0))

    # Comments on our own recent posts that we haven't replied to yet.
    pending = []
    try:
        mine = get(f"{me['id']}/media", ptoken, limit=15,
                   fields="caption,permalink,timestamp,comments{id,text,username,timestamp,replies{username}}").get("data", [])
        for m in mine:
            for c in m.get("comments", {}).get("data", []):
                if c.get("username") == me.get("username"):
                    continue
                replied = any(r.get("username") == me.get("username") for r in c.get("replies", {}).get("data", []))
                if not replied:
                    pending.append({"post": m.get("permalink"), "post_caption": (m.get("caption") or "")[:120],
                                    "from": c.get("username"), "text": c.get("text"), "when_utc": c.get("timestamp")})
    except ApiError as e:
        print(f"Could not read comments on our posts: {e}")

    data = {"scanned_utc": now.strftime("%Y-%m-%dT%H:%MZ"), "account": me.get("username"),
            "posts": posts, "awaiting_reply": pending, "accounts": accounts}
    with open(os.path.join(out, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    ist = now.astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    md = [f"# Comment queue — scanned {ist:%d %b, %I:%M %p} IST", "",
          f"## New posts from your list ({len(posts)})", ""]
    for p in posts:
        cap = " ".join(p["caption"].split())[:140]
        md.append(f"- **@{p['account']}** · {p['age_hours']}h ago · {p['likes']} likes · {p['comments']} comments  \n  {p['permalink']}  \n  _{cap}_")
    md += ["", f"## Comments on your posts awaiting a reply ({len(pending)})", ""]
    for c in pending:
        md.append(f"- **@{c['from']}**: {c['text']}  \n  {c['post']}")
    with open(os.path.join(out, "latest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    rep = ["# Target accounts", "", "| Account | Group | Followers | Status |", "|---|---|---|---|"]
    for a in accounts:
        status = "ok" if a["ok"] else "not found / not a professional account"
        rep.append(f"| @{a['handle']} | {a['group']} | {a.get('followers') or ''} | {status} |")
    with open(os.path.join(out, "accounts.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rep) + "\n")
    ok = sum(a["ok"] for a in accounts)
    print(f"\n{ok}/{len(accounts)} accounts found · {len(posts)} new posts · {len(pending)} comments awaiting reply")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
