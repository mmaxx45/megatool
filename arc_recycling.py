"""
ARC Raiders Item Recycling Database
Uses the MetaForge API to fetch all items and their recycling outputs.
Only includes items that are actually recyclable.

Data source: https://metaforge.app/arc-raiders
Attribution: MetaForge (metaforge.app/arc-raiders)
"""

import json
import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://metaforge.app"
API_URL = f"{BASE_URL}/api/arc-raiders/items"
ITEM_DATA_URL = f"{BASE_URL}/arc-raiders/database/item"

OUTPUT_JSON = "arc_raiders_recycling.json"
OUTPUT_CSV = "arc_raiders_recycling.csv"

REQUEST_DELAY = 0.2
MAX_WORKERS = 4


def get_api_item_count():
    """Quick check: how many items does the API currently have?"""
    resp = requests.get(API_URL, params={"page": 1, "limit": 1}, timeout=30)
    resp.raise_for_status()
    return resp.json()["pagination"]["total"]


def load_cache():
    """Load existing JSON cache if present."""
    if not os.path.exists(OUTPUT_JSON):
        return None
    try:
        with open(OUTPUT_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def fetch_all_items():
    """Fetch all items from the MetaForge API with pagination."""
    all_items = []
    page = 1

    while True:
        print(f"  Fetching items page {page}...")
        resp = requests.get(API_URL, params={"page": page, "limit": 50}, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        all_items.extend(data["data"])

        if not data["pagination"]["hasNextPage"]:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    print(f"  Total: {len(all_items)} items\n")
    return all_items


def resolve_value(data_array, ref):
    """Resolve a devalue reference. If ref is an int index, look it up."""
    if isinstance(ref, int) and 0 <= ref < len(data_array):
        val = data_array[ref]
        if isinstance(val, (str, int, float, bool)) or val is None:
            return val
    return ref


def parse_recycle_data(raw_json):
    """Parse SvelteKit devalue __data.json to extract recycling components."""
    try:
        nodes = raw_json.get("nodes", [])
        data_node = None
        for node in nodes:
            if isinstance(node, dict) and node.get("type") == "data":
                d = node.get("data", [])
                if isinstance(d, list) and len(d) > 100:
                    data_node = d
                    break

        if not data_node:
            return [], None, None

        page_schema = data_node[0]
        if not isinstance(page_schema, dict):
            return [], None, None

        total_recycle_value = resolve_value(data_node, page_schema.get("totalRecycleValue"))
        recycle_loss = resolve_value(data_node, page_schema.get("recycleValue"))

        rc_idx = page_schema.get("recycleComponentsDetails")
        if rc_idx is None:
            return [], total_recycle_value, recycle_loss

        component_indices = data_node[rc_idx]
        if not isinstance(component_indices, list):
            return [], total_recycle_value, recycle_loss

        components = []
        for comp_idx in component_indices:
            comp_dict = data_node[comp_idx]
            if not isinstance(comp_dict, dict):
                continue

            name = resolve_value(data_node, comp_dict.get("name", ""))
            quantity = resolve_value(data_node, comp_dict.get("quantity", 0))

            if name and isinstance(name, str):
                components.append({
                    "name": name,
                    "quantity": quantity if isinstance(quantity, (int, float)) else 0,
                })

        return components, total_recycle_value, recycle_loss
    except Exception:
        return [], None, None


def fetch_item_recycle(item_id):
    """Fetch recycling data for a single item from its __data.json page."""
    url = f"{ITEM_DATA_URL}/{item_id}/__data.json"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return parse_recycle_data(resp.json())
    except Exception:
        return [], None, None


def process_item(item):
    """Process a single item: fetch its recycling data."""
    time.sleep(REQUEST_DELAY)
    item_id = item["id"]
    components, total_value, loss = fetch_item_recycle(item_id)
    return item_id, components, total_value, loss


def build_result(item, components, total_val, loss):
    """Build a result dict for a recyclable item."""
    result = {
        "name": item["name"],
        "id": item["id"],
        "type": item.get("item_type", ""),
        "rarity": item.get("rarity", ""),
        "value": item.get("value", 0),
        "recycle_components": [
            {"name": c["name"], "quantity": int(c["quantity"])}
            for c in components
        ],
    }
    if total_val is not None:
        result["total_recycle_value"] = total_val
    if loss is not None:
        result["recycle_value_loss"] = loss
    return result


def save_outputs(results):
    """Save results to JSON and CSV."""
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"JSON saved to: {OUTPUT_JSON}")

    with open(OUTPUT_CSV, "w", encoding="utf-8") as f:
        f.write("Item Name,Item Type,Rarity,Value,Recycles Into,Recycle Value\n")
        for r in results:
            comps = "; ".join(
                f"{c['quantity']}x {c['name']}" for c in r["recycle_components"]
            )
            rv = r.get("total_recycle_value", "")
            name = r["name"].replace('"', '""')
            f.write(f'"{name}","{r["type"]}","{r["rarity"]}",{r["value"]},"{comps}","{rv}"\n')
    print(f"CSV saved to:  {OUTPUT_CSV}")


def print_results(results):
    """Print the recyclable items table."""
    print(f"\n{'ITEM':<40} {'TYPE':<22} {'RECYCLES INTO'}")
    print("-" * 100)

    for r in results:
        recycle_str = ", ".join(
            f"{c['quantity']}x {c['name']}" for c in r["recycle_components"]
        )
        total_val = r.get("total_recycle_value")
        if total_val is not None:
            recycle_str += f" (={total_val} coins)"
        print(f"{r['name']:<40} {r['type']:<22} {recycle_str}")

    print("-" * 100)
    print(f"Recyclable items: {len(results)}")
    print()


def fetch_recycle_for_items(items):
    """Fetch recycling data for a list of items, return only recyclable ones."""
    print(f"  Processing {len(items)} items ({MAX_WORKERS} parallel workers)...\n")

    results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_item, item): item for item in items}

        for future in as_completed(futures):
            item = futures[future]
            try:
                item_id, components, total_value, loss = future.result()
                if components:
                    results.append(build_result(item, components, total_value, loss))
            except Exception:
                pass

            completed += 1
            if completed % 50 == 0 or completed == len(items):
                pct = completed * 100 // len(items)
                print(f"  [{pct:3d}%] {completed}/{len(items)} items processed")

    results.sort(key=lambda r: r["name"])
    return results


def main():
    print("=" * 75)
    print("  ARC Raiders - Item Recycling Database")
    print("  Data source: MetaForge (https://metaforge.app/arc-raiders)")
    print("=" * 75)
    print()

    # Check API item count
    print("[1] Checking API for current item count...")
    api_total = get_api_item_count()
    print(f"  API reports {api_total} items.")

    # Check cache
    cached = load_cache()
    if cached is not None:
        # The cache only has recyclable items, but we stored the api total
        # alongside. We need to know the total item count at cache time.
        # We'll store it as metadata. If missing, fall back to full refresh.
        cache_meta_path = OUTPUT_JSON + ".meta"
        cached_total = None
        if os.path.exists(cache_meta_path):
            try:
                with open(cache_meta_path, "r") as f:
                    meta = json.load(f)
                cached_total = meta.get("api_total")
            except Exception:
                pass

        if cached_total is not None and cached_total == api_total:
            print(f"  Cache has {len(cached)} recyclable items (from {cached_total} total). Up to date!\n")
            print_results(cached)
            save_outputs(cached)
            return
        elif cached_total is not None:
            print(f"  Cache was built from {cached_total} items, API now has {api_total}. Fetching new items...\n")

            # Fetch all items from API, find which ones are new
            print("[2] Fetching full item list...")
            all_items = fetch_all_items()

            cached_ids = {r["id"] for r in cached}
            # Also track IDs we already know are NOT recyclable
            # We can infer: anything in all_items whose id is NOT in cached_ids
            # could be new OR could be a known non-recyclable.
            # To avoid re-checking known non-recyclables, store those too.
            known_non_recyclable = set()
            if os.path.exists(cache_meta_path):
                try:
                    with open(cache_meta_path, "r") as f:
                        meta = json.load(f)
                    known_non_recyclable = set(meta.get("non_recyclable_ids", []))
                except Exception:
                    pass

            known_ids = cached_ids | known_non_recyclable
            new_items = [item for item in all_items if item["id"] not in known_ids]

            if not new_items:
                print("  No new items found (count difference may be from removals).\n")
                # Rebuild from cached, removing any that no longer exist
                current_ids = {item["id"] for item in all_items}
                results = [r for r in cached if r["id"] in current_ids]
                results.sort(key=lambda r: r["name"])
            else:
                print(f"  Found {len(new_items)} new items to check.\n")
                print("[3] Fetching recycling data for new items...")
                new_results = fetch_recycle_for_items(new_items)
                new_non_recyclable = {
                    item["id"] for item in new_items
                } - {r["id"] for r in new_results}
                known_non_recyclable |= new_non_recyclable

                # Merge with existing cached results
                current_ids = {item["id"] for item in all_items}
                results = [r for r in cached if r["id"] in current_ids]
                results.extend(new_results)
                results.sort(key=lambda r: r["name"])

            print()
            print_results(results)
            save_outputs(results)

            # Update meta
            with open(cache_meta_path, "w") as f:
                json.dump({
                    "api_total": api_total,
                    "non_recyclable_ids": list(known_non_recyclable),
                }, f)
            return
        else:
            print("  Cache exists but no metadata. Doing full refresh.\n")

    # Full fetch (no usable cache)
    print("\n[2] Fetching all items from API...")
    all_items = fetch_all_items()

    print("[3] Fetching recycling data for all items...")
    results = fetch_recycle_for_items(all_items)

    # Track non-recyclable IDs for future incremental updates
    recyclable_ids = {r["id"] for r in results}
    non_recyclable_ids = [item["id"] for item in all_items if item["id"] not in recyclable_ids]

    print()
    print_results(results)
    save_outputs(results)

    # Save meta
    cache_meta_path = OUTPUT_JSON + ".meta"
    with open(cache_meta_path, "w") as f:
        json.dump({
            "api_total": api_total,
            "non_recyclable_ids": non_recyclable_ids,
        }, f)


if __name__ == "__main__":
    main()
