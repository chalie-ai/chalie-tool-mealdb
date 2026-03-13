"""
MealDB Tool Handler — Recipe search via TheMealDB API.

Four search modes (priority order):
  1. ingredient — filter.php?i=  (minimal results, needs lookup enrichment)
  2. category  — filter.php?c=  (minimal results, needs lookup enrichment)
  3. area      — filter.php?a=  (minimal results, needs lookup enrichment)
  4. query     — search.php?s=  (full results, no enrichment needed)

Filter endpoints return only id/name/thumb — full details fetched via
lookup.php?i={id} in parallel (ThreadPoolExecutor, max 3 workers).
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

_BASE_URL = "https://themealdb.com/api/json/v1/1"


def execute(topic: str, params: dict, config: dict = None, telemetry: dict = None) -> dict:
    """
    Search TheMealDB for recipes.

    Args:
        topic: Conversation topic (passed by framework, unused directly)
        params: {
            "query": str (required — meal name),
            "limit": int (optional, default 5, clamped 1-8),
            "ingredient": str (optional — search by ingredient),
            "category": str (optional — filter by category),
            "area": str (optional — filter by cuisine area)
        }
        config: Tool config from DB (unused — no API key needed)
        telemetry: Client telemetry dict (unused)

    Returns:
        {
            "results": [{"title", "category", "area", "instructions",
                         "ingredients", "image_url", "url", "tags",
                         "youtube_url"}],
            "count": int,
            "query": str,
            "_meta": {observability fields}
        }
    """
    query = (params.get("query") or "").strip()
    limit = max(1, min(8, int(params.get("limit") or 5)))
    ingredient = (params.get("ingredient") or "").strip()
    category = (params.get("category") or "").strip()
    area = (params.get("area") or "").strip()

    t0 = time.time()
    search_mode = "name"
    meals_raw = []

    try:
        if ingredient:
            search_mode = "ingredient"
            meals_raw = _fetch_filter("i", ingredient, limit)
        elif category:
            search_mode = "category"
            meals_raw = _fetch_filter("c", category, limit)
        elif area:
            search_mode = "area"
            meals_raw = _fetch_filter("a", area, limit)
        else:
            search_mode = "name"
            meals_raw = _fetch_search(query)
    except Exception as e:
        logger.error(
            '{"event":"mealdb_fetch_error","query":"%s","mode":"%s","error":"%s"}',
            query, search_mode, str(e)[:120],
        )
        return {
            "results": [], "count": 0, "query": query,
            "_meta": {"fetch_latency_ms": int((time.time() - t0) * 1000),
                      "result_count": 0, "search_mode": search_mode},
        }

    # Limit results
    meals_raw = meals_raw[:limit]

    # Normalize into output format
    results = [_normalize_meal(m) for m in meals_raw]
    results = [r for r in results if r.get("title")]

    fetch_latency_ms = int((time.time() - t0) * 1000)

    logger.info(
        '{"event":"mealdb_ok","query":"%s","mode":"%s","count":%d,"latency_ms":%d}',
        query, search_mode, len(results), fetch_latency_ms,
    )

    return {
        "results": results,
        "count": len(results),
        "query": query,
        "_meta": {
            "fetch_latency_ms": fetch_latency_ms,
            "result_count": len(results),
            "search_mode": search_mode,
        },
    }


# -- Search by name (full results) --------------------------------------------

def _fetch_search(query: str) -> list:
    """Search by meal name — returns full meal objects."""
    import requests

    if not query:
        return []

    resp = requests.get(
        f"{_BASE_URL}/search.php",
        params={"s": query},
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0 (compatible; mealdb-tool/1.0)"},
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("meals") or []


# -- Filter endpoints (minimal results, need enrichment) -----------------------

def _fetch_filter(filter_key: str, value: str, limit: int) -> list:
    """
    Fetch from filter endpoint, then enrich top N via lookup.

    Filter endpoints return: [{"strMeal", "strMealThumb", "idMeal"}]
    We need full details via lookup.php?i={idMeal}.
    """
    import requests

    resp = requests.get(
        f"{_BASE_URL}/filter.php",
        params={filter_key: value},
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0 (compatible; mealdb-tool/1.0)"},
    )
    resp.raise_for_status()
    data = resp.json()
    filtered = data.get("meals") or []

    if not filtered:
        return []

    # Take top N and enrich with full details in parallel
    to_enrich = filtered[:limit]
    return _enrich_meals(to_enrich)


def _enrich_meals(meals: list) -> list:
    """Batch-fetch full meal details by ID using ThreadPoolExecutor."""
    enriched = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_lookup_meal, m["idMeal"]): m
            for m in meals if m.get("idMeal")
        }
        for future in as_completed(futures, timeout=10):
            try:
                result = future.result(timeout=3)
                if result:
                    enriched.append(result)
                else:
                    # Lookup returned empty — use partial data from filter
                    fallback = futures[future]
                    enriched.append({
                        "idMeal": fallback.get("idMeal", ""),
                        "strMeal": fallback.get("strMeal", ""),
                        "strMealThumb": fallback.get("strMealThumb", ""),
                    })
            except Exception as e:
                # Lookup failed — use partial data from filter
                fallback = futures[future]
                logger.debug(
                    '{"event":"mealdb_lookup_failed","id":"%s","error":"%s"}',
                    fallback.get("idMeal", ""), str(e)[:80],
                )
                enriched.append({
                    "idMeal": fallback.get("idMeal", ""),
                    "strMeal": fallback.get("strMeal", ""),
                    "strMealThumb": fallback.get("strMealThumb", ""),
                })

    return enriched


def _lookup_meal(meal_id: str) -> dict | None:
    """Fetch full meal details by ID."""
    import requests

    resp = requests.get(
        f"{_BASE_URL}/lookup.php",
        params={"i": meal_id},
        timeout=3,
        headers={"User-Agent": "Mozilla/5.0 (compatible; mealdb-tool/1.0)"},
    )
    resp.raise_for_status()
    data = resp.json()
    meals = data.get("meals") or []
    return meals[0] if meals else None


# -- Normalization -------------------------------------------------------------

def _extract_ingredients(meal: dict) -> list:
    """
    Extract ingredient/measure pairs from MealDB's strIngredient1..20 + strMeasure1..20.
    Returns [{"ingredient": str, "measure": str}] for non-empty pairs.
    """
    ingredients = []
    for i in range(1, 21):
        ingredient = (meal.get(f"strIngredient{i}") or "").strip()
        measure = (meal.get(f"strMeasure{i}") or "").strip()
        if ingredient:
            ingredients.append({"ingredient": ingredient, "measure": measure})
    return ingredients


def _normalize_meal(meal: dict) -> dict:
    """Normalize a raw MealDB meal dict into the canonical output shape."""
    title = (meal.get("strMeal") or "").strip()
    cat = (meal.get("strCategory") or "").strip()
    area = (meal.get("strArea") or "").strip()
    instructions = (meal.get("strInstructions") or "").strip()
    image_url = (meal.get("strMealThumb") or "").strip()
    url = (meal.get("strSource") or "").strip()
    youtube_url = (meal.get("strYoutube") or "").strip()
    tags_raw = (meal.get("strTags") or "").strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

    ingredients = _extract_ingredients(meal)

    return {
        "title": title,
        "category": cat,
        "area": area,
        "instructions": instructions,
        "ingredients": ingredients,
        "image_url": image_url,
        "url": url,
        "tags": tags,
        "youtube_url": youtube_url,
    }
