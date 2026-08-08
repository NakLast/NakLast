#!/usr/bin/env python3
import base64
import datetime
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DATA_FILE = os.path.join(DATA_DIR, "wakatime.json")
README_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")
BLOCKS = "⣀⣄⣤⣦⣶⣷⣿"
BAR_LENGTH = 25


def import_raw_dump(data, filepath):
    try:
        print(f"Importing raw WakaTime export from {filepath}...")
        with open(filepath, "r", encoding="utf-8") as f:
            dump = json.load(f)

        days = dump.get("days", [])
        if not days:
            print("No 'days' array found in export file.")
            return False

        daily_history = data.setdefault("daily_history", {})
        imported_count = 0

        for day in days:
            date_str = day.get("date")
            if not date_str:
                continue

            grand_total = day.get("grand_total", {}).get("total_seconds", 0)
            langs = {}
            for l in day.get("languages", []):
                name = l.get("name")
                secs = l.get("total_seconds", 0)
                if name and secs > 0:
                    langs[name] = secs

            daily_history[date_str] = {
                "grand_total_seconds": grand_total,
                "languages": langs
            }
            imported_count += 1

        all_dates = sorted(daily_history.keys())
        if all_dates:
            data["start_date"] = all_dates[0]

        # Clean up temporary base seeds once full historical days are imported
        data.pop("base_seconds", None)
        data.pop("base_total_seconds", None)

        print(f"Successfully imported {imported_count} days of history (Earliest: {data.get('start_date')}).")
        return True
    except Exception as e:
        print(f"Error importing {filepath}: {e}")
        return False


def load_data():
    data = None
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Warning: Could not parse {DATA_FILE}: {e}")

    if data is None:
        data = {
            "start_date": "2024-02-29",
            "daily_history": {},
            "last_updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        }

    # Check for any raw export dumps to auto-import
    raw_dumps = glob.glob(os.path.join(DATA_DIR, "wakatime-*.json"))
    for dump_path in raw_dumps:
        import_raw_dump(data, dump_path)

    return data


def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Saved metrics data to {DATA_FILE}")


def fetch_wakatime_summaries(api_key):
    if not api_key:
        print("No WAKATIME_API_KEY provided; skipping API fetch and using local accumulated data.")
        return []

    today = datetime.datetime.now(datetime.timezone.utc).date()
    start_date = today - datetime.timedelta(days=14)

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    url = f"https://wakatime.com/api/v1/users/current/summaries?start={start_str}&end={end_str}&api_key={api_key}"

    headers = {
        "User-Agent": "WakaTime-Stats-Updater/1.0",
        "Authorization": f"Basic {base64.b64encode(api_key.encode('utf-8')).decode('utf-8')}"
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("data", [])
    except urllib.error.HTTPError as e:
        print(f"HTTP Error fetching WakaTime summaries ({e.code}): {e.reason}")
        try:
            body = e.read().decode("utf-8")
            print(f"Response body: {body}")
        except Exception:
            pass
        return []
    except Exception as e:
        print(f"Error fetching WakaTime summaries: {e}")
        return []


def update_data_with_summaries(data, summaries):
    daily_history = data.setdefault("daily_history", {})

    for day in summaries:
        range_info = day.get("range", {})
        date_str = range_info.get("date")
        if not date_str:
            continue

        grand_total = day.get("grand_total", {}).get("total_seconds", 0)
        languages = {}
        for lang in day.get("languages", []):
            name = lang.get("name")
            secs = lang.get("total_seconds", 0)
            if name and secs > 0:
                languages[name] = secs

        daily_history[date_str] = {
            "grand_total_seconds": grand_total,
            "languages": languages
        }

    data["last_updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def compute_metrics(data):
    daily_history = data.get("daily_history", {})
    base_seconds = data.get("base_seconds", {}) if not daily_history else {}
    base_total = data.get("base_total_seconds", sum(base_seconds.values())) if not daily_history else 0

    # Language totals
    lang_totals = dict(base_seconds)
    for date_str, day_data in daily_history.items():
        for lang, secs in day_data.get("languages", {}).items():
            lang_totals[lang] = lang_totals.get(lang, 0) + secs

    # Overall total seconds
    daily_total = sum(day_data.get("grand_total_seconds", 0) for day_data in daily_history.values())
    overall_total_seconds = base_total + daily_total

    sum_lang_totals = sum(lang_totals.values())
    if overall_total_seconds < sum_lang_totals:
        overall_total_seconds = sum_lang_totals

    return lang_totals, overall_total_seconds


def generate_progress_bar(pct, width=BAR_LENGTH, blocks=BLOCKS):
    val = (pct / 100.0) * width
    full_count = int(val)
    fraction = val - full_count

    if fraction > 0 and full_count < width:
        fract_idx = min(len(blocks) - 1, int(fraction * 6))
        partial_block = blocks[fract_idx]
        empty_count = width - full_count - 1
        return (blocks[-1] * full_count) + partial_block + (blocks[0] * empty_count)
    else:
        empty_count = width - full_count
        return (blocks[-1] * full_count) + (blocks[0] * empty_count)


def generate_stats_markdown(data):
    lang_totals, overall_total_seconds = compute_metrics(data)

    all_dates = sorted(data.get("daily_history", {}).keys())
    start_date_str = data.get("start_date") or (all_dates[0] if all_dates else "2024-02-29")
    try:
        start_date_obj = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
        formatted_start = start_date_obj.strftime("%d %B %Y")
    except Exception:
        formatted_start = start_date_str

    now = datetime.datetime.now(datetime.timezone.utc)
    formatted_end = now.strftime("%d %B %Y")

    total_hrs = int(overall_total_seconds // 3600)
    total_mins = int((overall_total_seconds % 3600) // 60)

    # Sort languages by time descending
    sorted_langs = sorted(lang_totals.items(), key=lambda x: x[1], reverse=True)
    # Take top languages
    top_langs = [item for item in sorted_langs if item[1] > 0][:5]

    lines = []
    lines.append("```txt")
    lines.append(f"From: {formatted_start} - To: {formatted_end}")
    lines.append("")
    lines.append(f"Total Time: {total_hrs:,} hrs {total_mins:02d} mins")
    lines.append("")

    for lang, secs in top_langs:
        pct = (secs / overall_total_seconds * 100.0) if overall_total_seconds > 0 else 0.0
        hrs = int(secs // 3600)
        mins = int((secs % 3600) // 60)
        time_str = f"{hrs:,} hrs {mins:02d} mins"
        bar = generate_progress_bar(pct)

        line = f"{lang:<26} {time_str:<21} {bar}   {pct:05.2f} %"
        lines.append(line)

    lines.append("```")
    return "\n".join(lines)


def update_readme(stats_content):
    if not os.path.exists(README_FILE):
        print(f"Error: {README_FILE} not found.")
        return False

    with open(README_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    start_tag = "<!--START_SECTION:waka-->"
    end_tag = "<!--END_SECTION:waka-->"

    pattern = re.compile(f"{re.escape(start_tag)}(.*?){re.escape(end_tag)}", re.DOTALL)

    if not pattern.search(content):
        print(f"Error: Could not find '{start_tag}' and '{end_tag}' in {README_FILE}")
        return False

    new_section = f"{start_tag}\n\n{stats_content}\n\n{end_tag}"
    updated_content = pattern.sub(new_section, content)

    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(updated_content)

    print(f"Successfully updated {README_FILE}")
    return True


def main():
    api_key = os.environ.get("WAKATIME_API_KEY", "").strip()
    data = load_data()

    if api_key:
        print("Fetching latest summaries from WakaTime API...")
        summaries = fetch_wakatime_summaries(api_key)
        if summaries:
            print(f"Fetched {len(summaries)} daily summary records.")
            update_data_with_summaries(data, summaries)
        else:
            print("No new summaries received from API.")
    else:
        print("WAKATIME_API_KEY not set in environment. Running with local store.")

    save_data(data)
    stats_md = generate_stats_markdown(data)
    print("\n--- Generated Stats Block ---")
    print(stats_md)
    print("-----------------------------\n")

    update_readme(stats_md)


if __name__ == "__main__":
    main()
