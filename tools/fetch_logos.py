#!/usr/bin/env python3
"""
fetch_logos.py  —  one-time sponsor-logo downloader
====================================================

The league's sponsor logos live in Tom's Google Drive. This script pulls each
one down into  static/logos/  so they're committed to the repo and served
locally by the site. That means the logos render reliably everywhere (live
site AND any preview) with no hot-linking to Google and no flaky throttling.

You only need to run this once (and again any time a sponsor logo changes).

    cd jssa-website
    python tools/fetch_logos.py

It uses only the Python standard library — no pip install needed.
After it finishes, commit the new files:

    git add static/logos
    git commit -m "Add sponsor logos"
    git push

Any sponsor without a Drive logo (or whose download fails) simply keeps the
site's built-in fallback: the business's brand mark by domain, then a clean
name card. Nothing breaks.
"""

import os
import sys
import urllib.request

# slug  ->  Google Drive file id
# (slug must match the /static/logos/<slug>.png paths used in templates/index.html)
LOGOS = {
    "american-sr-health":  "1le3nK1MdPqY8qH0Tgzb1BUjbjMuyTB4E",
    "panera":              "1pAOFUafHOkAH6fHIFeJjWTU4PwPMlIAM",
    "stephen-denny":       "1Th9uRAjEfW-vhLkDisx1DBCJBvh0Z8Xc",
    "golf-club":           "1_e6wYNqDJOGEsqDDudUpvrFIPqK_t5sE",
    "royal-cafe":          "166z3zdu8zPfTwJfj-m7enarLRooCTCaY",
    "team1-sports":        "17zkWfSVqNtZ2Uvhe-L4IyhT2VYF275Bo",
    "uncle-micks":         "1jU_X9_jiqiDKENNJiaY2Z1ucDs09S-tz",
    "1000-north":          "1WVIh0rPpM3U1SKXwvHxSRn2jsF8ICsXm",
    "cindy-sojka":         "1Gffs-pHQ0Cfxqun3FiaKa_ExsEjmhezC",
    "mike-parenti":        "1Ewyhqu9_JMxo6MZOxhnboelNIzKBPzxw",
    "food-shack":          "1Wih3oO98Az7QhVmhkrNa8_k1E1FzelwT",
    "se-rods":             "1pft9Wt4I3g1rZiIkDGHKZju7rpBGPXFF",
    "village-scoop-shack": "1xza0JCWClLBISuFTRjSjbPTDFC-RY6n8",
}

# Save next to the repo's static/logos folder regardless of where you run from.
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "static", "logos"))

# Magic-byte signatures so we can tell a real image from an HTML error page.
IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",   # PNG
    b"\xff\xd8\xff",         # JPEG
    b"GIF87a", b"GIF89a",    # GIF
    b"RIFF",                 # WEBP (RIFF....WEBP)
)

UA = {"User-Agent": "Mozilla/5.0 (jssa-logo-fetcher)"}


def candidate_urls(file_id):
    return [
        f"https://drive.google.com/thumbnail?id={file_id}&sz=w600",
        f"https://drive.google.com/uc?export=download&id={file_id}",
        f"https://lh3.googleusercontent.com/d/{file_id}=w600",
    ]


def looks_like_image(data):
    if not data or len(data) < 100:
        return False
    return any(data.startswith(sig) for sig in IMAGE_SIGNATURES)


def fetch(file_id):
    for url in candidate_urls(file_id):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if looks_like_image(data):
                return data
        except Exception:
            continue
    return None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Saving logos to: {OUT_DIR}\n")

    ok, failed = [], []
    for slug, file_id in LOGOS.items():
        data = fetch(file_id)
        if data:
            dest = os.path.join(OUT_DIR, f"{slug}.png")
            with open(dest, "wb") as f:
                f.write(data)
            ok.append(slug)
            print(f"  [ok]   {slug:<22} {len(data):>7,} bytes")
        else:
            failed.append(slug)
            print(f"  [skip] {slug:<22} could not download (will use fallback)")

    print(f"\nDone. {len(ok)} downloaded, {len(failed)} skipped.")
    if failed:
        print("Skipped (site falls back to brand-mark/name automatically):")
        for s in failed:
            print(f"  - {s}")
    print("\nNext:")
    print("  git add static/logos")
    print('  git commit -m "Add sponsor logos"')
    print("  git push")

    # Non-zero exit only if EVERY logo failed (likely a network/permissions issue).
    if ok:
        return 0
    print("\nNothing downloaded — check your internet connection and that the "
          "Drive files are still shared as 'anyone with the link'.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
