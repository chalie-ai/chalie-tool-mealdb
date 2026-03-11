"""
MealDB Tool Runner — Generates an inline HTML carousel card with rich recipe views.

Each slide shows a hero image, title, meta line, summary, collapsible ingredients,
collapsible steps, and footer links. JS wiring lives in
frontend/interface/cards/tool_result.js (data-carousel convention).
Outputs formalized IPC contract: {"text": str, "html": str}
"""

import sys
import json
import base64
from html import escape
from handler import execute


# -- SVG icons (inline, no external resources) ---------------------------------

_LINK_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round" '
    'style="vertical-align:middle;flex-shrink:0;">'
    '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
    '<polyline points="15 3 21 3 21 9"/>'
    '<line x1="10" y1="14" x2="21" y2="3"/>'
    '</svg>'
)

_CHEVRON_LEFT = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="15 18 9 12 15 6"/>'
    '</svg>'
)

_CHEVRON_RIGHT = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="9 18 15 12 9 6"/>'
    '</svg>'
)

_CHEVRON_DOWN = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round" '
    'style="vertical-align:middle;margin-left:4px;transition:transform 220ms ease;">'
    '<polyline points="6 9 12 15 18 9"/>'
    '</svg>'
)

_PLAY_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round" '
    'style="vertical-align:middle;flex-shrink:0;">'
    '<polygon points="5 3 19 12 5 21 5 3"/>'
    '</svg>'
)

# Radiant design palette constants
_ACCENT = "#FF6B35"           # Warm orange — recipe accent
_ACCENT_BG = "rgba(255,107,53,0.15)"
_TEXT_PRIMARY = "#eae6f2"
_TEXT_SECONDARY = "rgba(234,230,242,0.58)"
_TEXT_TERTIARY = "rgba(234,230,242,0.38)"
_SURFACE = "rgba(255,255,255,0.04)"
_BORDER = "rgba(255,255,255,0.07)"
_DOT_ACTIVE = "#8A5CFF"       # Violet — shared carousel convention
_DOT_INACTIVE = "rgba(255,255,255,0.25)"


# -- Details/summary styling (injected once per card) --------------------------

_DETAILS_STYLE = (
    '<style>'
    '.mdb-details summary{cursor:pointer;list-style:none;display:flex;'
    'align-items:center;gap:4px;}'
    '.mdb-details summary::-webkit-details-marker{display:none;}'
    '.mdb-details summary::marker{display:none;content:"";}'
    '.mdb-details[open] summary .mdb-chev{transform:rotate(180deg);}'
    '.mdb-step-num{display:inline-flex;align-items:center;justify-content:center;'
    f'width:20px;height:20px;border-radius:50%;background:{_ACCENT_BG};'
    f'color:{_ACCENT};font-size:11px;font-weight:600;flex-shrink:0;}}'
    '</style>'
)


# -- Slide rendering -----------------------------------------------------------

def _render_slide(meal: dict, visible: bool) -> str:
    title = meal.get("title") or ""
    cat = meal.get("category") or ""
    area = meal.get("area") or ""
    instructions = meal.get("instructions") or ""
    image_url = meal.get("image_url") or ""
    url = meal.get("url") or ""
    youtube_url = meal.get("youtube_url") or ""
    ingredients = meal.get("ingredients") or []

    display = "flex" if visible else "none"

    # Hero image with gradient overlay
    img_html = ""
    if image_url:
        img_html = (
            f'<div style="position:relative;width:100%;aspect-ratio:16/9;overflow:hidden;'
            f'border-radius:8px 8px 0 0;background:{_SURFACE};">'
            f'<img src="{escape(image_url)}" alt="" loading="lazy" '
            f'style="width:100%;height:100%;object-fit:cover;display:block;" />'
            f'<div style="position:absolute;bottom:0;left:0;right:0;height:40%;'
            f'background:linear-gradient(transparent,rgba(0,0,0,0.4));"></div>'
            f'</div>'
        )

    # Meta line: area . category
    meta_parts = []
    if area:
        meta_parts.append(escape(area))
    if cat:
        meta_parts.append(escape(cat))
    meta_html = ""
    if meta_parts:
        sep = f' <span style="color:rgba(234,230,242,0.2);">\u00b7</span> '
        meta_html = (
            f'<div style="font-size:11px;color:{_TEXT_TERTIARY};margin-bottom:4px;">'
            + sep.join(meta_parts)
            + '</div>'
        )

    # Summary: first ~120 chars of instructions
    summary_html = ""
    if instructions:
        summary_text = instructions[:120].rstrip()
        if len(instructions) > 120:
            # Trim to last space to avoid mid-word cut
            last_space = summary_text.rfind(" ")
            if last_space > 60:
                summary_text = summary_text[:last_space]
            summary_text += "\u2026"
        summary_html = (
            f'<p style="font-size:13px;color:{_TEXT_SECONDARY};'
            f'line-height:1.55;margin:0 0 8px 0;">{escape(summary_text)}</p>'
        )

    # Collapsible: Ingredients
    ingredients_html = ""
    if ingredients:
        rows = ""
        for ing in ingredients:
            measure = escape(ing.get("measure") or "")
            ingredient = escape(ing.get("ingredient") or "")
            rows += (
                f'<div style="display:flex;gap:8px;padding:3px 0;">'
                f'<span style="color:{_TEXT_TERTIARY};font-size:12px;min-width:70px;'
                f'text-align:right;flex-shrink:0;">{measure}</span>'
                f'<span style="color:{_TEXT_SECONDARY};font-size:12px;">{ingredient}</span>'
                f'</div>'
            )
        ingredients_html = (
            f'<details class="mdb-details" style="margin-top:6px;">'
            f'<summary style="font-size:12px;color:{_TEXT_TERTIARY};'
            f'padding:4px 0;user-select:none;">'
            f'Ingredients ({len(ingredients)})'
            f'<span class="mdb-chev">{_CHEVRON_DOWN}</span>'
            f'</summary>'
            f'<div style="background:{_SURFACE};border:1px solid {_BORDER};'
            f'border-radius:6px;padding:8px 10px;margin-top:4px;">'
            + rows
            + '</div></details>'
        )

    # Collapsible: Steps
    steps_html = ""
    if instructions:
        # Split on double newlines or numbered patterns, then filter empties
        raw_steps = [s.strip() for s in instructions.replace("\r\n", "\n").split("\n") if s.strip()]
        step_rows = ""
        step_num = 0
        for step in raw_steps:
            step_num += 1
            step_rows += (
                f'<div style="display:flex;gap:10px;align-items:flex-start;'
                f'padding:5px 0;">'
                f'<span class="mdb-step-num">{step_num}</span>'
                f'<span style="font-size:13px;color:{_TEXT_SECONDARY};'
                f'line-height:1.65;flex:1;">{escape(step)}</span>'
                f'</div>'
            )
        steps_html = (
            f'<details class="mdb-details" style="margin-top:4px;">'
            f'<summary style="font-size:12px;color:{_TEXT_TERTIARY};'
            f'padding:4px 0;user-select:none;">'
            f'Steps'
            f'<span class="mdb-chev">{_CHEVRON_DOWN}</span>'
            f'</summary>'
            f'<div style="background:{_SURFACE};border:1px solid {_BORDER};'
            f'border-radius:6px;padding:8px 12px;margin-top:4px;">'
            + step_rows
            + '</div></details>'
        )

    # Footer links
    links = []
    if url:
        links.append(
            f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer" '
            f'style="display:inline-flex;align-items:center;gap:5px;'
            f'color:{_ACCENT};font-size:12px;text-decoration:none;opacity:0.85;">'
            + _LINK_ICON
            + '<span>Full recipe</span></a>'
        )
    if youtube_url:
        links.append(
            f'<a href="{escape(youtube_url)}" target="_blank" rel="noopener noreferrer" '
            f'style="display:inline-flex;align-items:center;gap:5px;'
            f'color:{_ACCENT};font-size:12px;text-decoration:none;opacity:0.85;">'
            + _PLAY_ICON
            + '<span>Watch video</span></a>'
        )
    footer_html = ""
    if links:
        footer_html = (
            f'<div style="display:flex;gap:14px;margin-top:8px;">'
            + "".join(links)
            + '</div>'
        )

    return (
        f'<div data-slide '
        f'style="display:{display};flex-direction:column;'
        f'background:{_SURFACE};border-radius:9px;border:1px solid {_BORDER};'
        f'overflow:hidden;">'
        + img_html
        + f'<div style="padding:14px 16px;">'
        + meta_html
        + f'<div style="font-weight:600;font-size:15px;color:{_TEXT_PRIMARY};'
          f'line-height:1.3;margin-bottom:5px;">{escape(title)}</div>'
        + summary_html
        + ingredients_html
        + steps_html
        + footer_html
        + '</div>'
        + '</div>'
    )


# -- Navigation ----------------------------------------------------------------

def _render_navigation(count: int) -> str:
    """Carousel nav buttons + dot indicators. Matches shared convention exactly."""
    btn_style = (
        f"background:{_SURFACE};border:1px solid rgba(255,255,255,0.12);"
        "border-radius:50%;width:28px;height:28px;display:inline-flex;align-items:center;"
        "justify-content:center;cursor:pointer;color:rgba(234,230,242,0.7);padding:0;"
        "flex-shrink:0;outline:none;"
        "transition:background 220ms ease,border-color 220ms ease,color 220ms ease;"
    )

    dots = "".join(
        f'<span data-dot style="'
        + (
            f"width:7px;height:7px;border-radius:50%;background:{_DOT_ACTIVE};"
            "transform:scale(1.2);flex-shrink:0;cursor:pointer;transition:all 220ms ease;"
            if i == 0 else
            f"width:7px;height:7px;border-radius:50%;background:{_DOT_INACTIVE};"
            "flex-shrink:0;cursor:pointer;transition:all 220ms ease;"
        )
        + '"></span>'
        for i in range(count)
    )

    return (
        '<div style="display:flex;align-items:center;justify-content:center;'
        'gap:8px;margin-top:10px;">'
        + f'<button type="button" data-prev style="{btn_style}">{_CHEVRON_LEFT}</button>'
        + f'<div style="display:flex;align-items:center;gap:5px;">{dots}</div>'
        + f'<button type="button" data-next style="{btn_style}">{_CHEVRON_RIGHT}</button>'
        + '</div>'
    )


# -- Card assembly -------------------------------------------------------------

def _render_html(results: list) -> str:
    """Assemble the full carousel card. Hard-capped at 8 slides."""
    results = results[:8]
    if not results:
        return (
            f'<p style="color:{_TEXT_TERTIARY};font-size:13px;'
            f'font-family:system-ui,-apple-system,sans-serif;padding:12px 14px;margin:0;">'
            f'No recipes found.</p>'
        )

    slides = "".join(_render_slide(r, i == 0) for i, r in enumerate(results))
    nav = _render_navigation(len(results)) if len(results) > 1 else ""

    return (
        _DETAILS_STYLE
        + '<div data-carousel '
        'style="font-family:system-ui,-apple-system,sans-serif;">'
        + slides
        + nav
        + '</div>'
    )


# -- Text for LLM synthesis ---------------------------------------------------

def _format_text(results: list, query: str) -> str:
    """
    Structured text output — this is what the LLM receives for synthesis.
    Includes full ingredients and instructions so the LLM can produce
    a helpful cooking narrative.
    """
    if not results:
        return (
            f'No recipes found for "{query}". '
            f'Try a different dish name, ingredient, or browse by category.'
        )

    lines = [f'Recipe results for "{query}":']
    for i, r in enumerate(results, 1):
        lines.append(f"\n{i}. {r.get('title', '')}")
        cat = r.get("category", "")
        area = r.get("area", "")
        if cat or area:
            meta = " - ".join(p for p in [area, cat] if p)
            lines.append(f"   Cuisine: {meta}")
        if tags := r.get("tags", []):
            lines.append(f"   Tags: {', '.join(tags)}")
        if ingredients := r.get("ingredients", []):
            ing_strs = [
                f"{ing['measure']} {ing['ingredient']}".strip()
                for ing in ingredients
            ]
            lines.append(f"   Ingredients: {', '.join(ing_strs)}")
        if instructions := r.get("instructions", ""):
            lines.append(f"   Instructions: {instructions}")
        if url := r.get("url", ""):
            lines.append(f"   Source: {url}")
        if yt := r.get("youtube_url", ""):
            lines.append(f"   Video: {yt}")
    return "\n".join(lines)


# -- Entry point ---------------------------------------------------------------

payload = json.loads(base64.b64decode(sys.argv[1]))
params = payload.get("params", {})
settings = payload.get("settings", {})
telemetry = payload.get("telemetry", {})

result = execute(topic="", params=params, config=settings, telemetry=telemetry)
results = result.get("results", [])

output = {
    "results": results,
    "count": result.get("count", 0),
    "query": result.get("query", ""),
    "text": _format_text(results, result.get("query", "")),
    "html": _render_html(results) if results else None,
    "_meta": result.get("_meta", {}),
}

print(json.dumps(output))
