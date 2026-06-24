"""Self-contained HTML export of an analysis stats dict.

Produces a single .html file that mirrors the on-screen analysis layout:
header, RoundX cards, core stats, service-fault matrix, chain analysis.
Both "Per team" and "Per player" views are emitted (stacked) since HTML
has no spatial constraint — the in-app toggle exists only because the
window can be narrow.

The Game Flow chart and title-card flow strip are bitmap-rendered by the
caller (see `_grab_widget_b64` in `analysis_screen`) and passed in as
base64 PNG strings — keeping this module pure-Python with no Qt
dependency, so it stays unit-testable in isolation.

Colour palette is copied from the on-screen widgets so the export looks
visually identical at a glance: dark `#0e0e22` background, panels on
`#16162a`, gold (`#ffd700`) for match-card and break-point accents, blue
(`#5fb3ff`) and orange (`#ffa566`) for team A/B respectively.
"""

from __future__ import annotations

import html as html_lib
from datetime import datetime
from typing import Optional

from app.config import FAULT_TYPES
from app.core.core_stats_definitions import CORE_STAT_DEFINITIONS


# Mirrors the in-app palette so the exported file matches the screen.
PALETTE = {
    "bg":          "#0e0e22",
    "panel":       "#16162a",
    "panel2":      "#1a1a36",
    "panel_alt":   "#12122a",
    "header_bg":   "#1f1f3a",
    "grid":        "#2a2a55",
    "sub_grid":    "#22224a",
    "border":      "#2a2a55",
    "text":        "#e0e0e0",
    "label":       "#aaaacc",
    "dim":         "#555577",
    "dim2":        "#8888aa",
    "summary":     "#a0c4ff",
    "hot":         "#ffd6a5",
    "winner":      "#7adb7a",
    "loser":       "#8888aa",
    "neutral":     "#e0e0e0",
    "pos":         "#7adb7a",
    "neg":         "#ff7070",
    "zero":        "#ffd6a5",
    "match_edge":  "#ffd700",
    "team_a":      "#5fb3ff",
    "team_b":      "#ffa566",
}


# --------------------------------------------------------------------- #
#  Formatting helpers — same rules as analysis_screen.py
# --------------------------------------------------------------------- #


def _esc(s) -> str:
    return html_lib.escape(str(s))


def _fmt_pct(value, places: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{places}f}%"


def _fmt_signed_pp(value, places: int = 0) -> str:
    if value is None:
        return ""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.{places}f}pp"


def _ratio_parts(num: int, den: int) -> tuple[str, str]:
    """Return (main, sub) — '21/15' + '75%' — or ('—', '') when den is 0."""
    if den <= 0:
        return "—", ""
    return f"{num}/{den}", f"{(num/den) * 100:.0f}%"


def _fmt_pair(a: int, b: int) -> str:
    return f"{a}/{b}"


def _fmt_date(iso) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(str(iso)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(iso)


def _heat_color(value: int) -> str:
    """Fault-count heat scale — mirrors `analysis_screen._heat`."""
    if value <= 0:
        return PALETTE["dim"]
    stops = [(0, "#555577"), (1, "#ffd6a5"), (2, "#ffb677"),
             (4, "#e07b39"), (7, "#c44a60")]
    chosen = stops[0][1]
    for threshold, color in stops:
        if value >= threshold:
            chosen = color
    return chosen


def _color_for_vs_baseline(vs) -> str:
    """Mirrors `analysis_screen._color_for_vs_baseline`."""
    if vs is None:
        return PALETTE["hot"]
    if vs > 0.05:
        return "#ff7070"
    if vs > 0.01:
        return "#ff9090"
    if vs < -0.05:
        return "#7adb7a"
    if vs < -0.01:
        return "#90ee90"
    return PALETTE["hot"]


# --------------------------------------------------------------------- #
#  Stat-row specs — duplicated from analysis_screen._stat_rows() so the
#  exporter stays decoupled from the UI module. If you add or rename a
#  row here, update the UI spec too (and vice versa).
# --------------------------------------------------------------------- #


def _stat_rows() -> list[tuple]:
    return [
        ("Aces / Aced",
         lambda s: ("pair", s["aces"], s["aced"]),
         lambda s: s["aces"] == 0 and s["aced"] == 0),
        ("Breaks/Broken",
         lambda s: ("pair", s["breaks_for"], s["breaks_against"]),
         lambda s: s["breaks_for"] == 0 and s["breaks_against"] == 0),
        ("Holds",
         lambda s: ("ratio", s["holds"], s["total_receives"]),
         lambda s: s["holds"] == 0),
        ("Side-outs",
         lambda s: ("ratio", s["side_outs"], s["total_receives"]),
         lambda s: s["side_outs"] == 0),
        ("Clean Side-outs",
         lambda s: ("ratio", s["clean_side_outs"], s["total_receives"]),
         lambda s: s["clean_side_outs"] == 0),
        ("Errors",
         lambda s: ("plain", s["total_errors"]),
         lambda s: s["total_errors"] == 0),
        ("Hit Errors",
         lambda s: ("ratio", s["hit_errors_total"], s["hits"]),
         lambda s: s["hit_errors_total"] == 0),
        ("Set Errors",
         lambda s: ("ratio", s["set_errors_total"], s["sets"]),
         lambda s: s["set_errors_total"] == 0),
        ("Double Faults",
         lambda s: ("ratio", s["double_faults_total"], s["points_served"]),
         lambda s: s["double_faults_total"] == 0),
        ("First Fault",
         lambda s: ("ratio", s["single_faults_total"], s["points_served"]),
            lambda s: s["single_faults_total"] == 0),
           ("Finish Hits",
            lambda s: ("ratio", s["finish_hits"], s["hits"]),
            lambda s: s["finish_hits"] == 0),
           ("Touches",
            lambda s: ("ratio", s["total_touches"], s["opponent_hits"]),
            lambda s: s["total_touches"] == 0),
           ("Weak Touches",
            lambda s: ("ratio", s["weak_touches"], s["total_touches"]),
            lambda s: s["weak_touches"] == 0),
           ("Weak Receives",
            lambda s: ("ratio", s["weak_receives"], s["total_receives"]),
            lambda s: s["weak_receives"] == 0),
           ("Weak Sets",
            lambda s: ("ratio", s["weak_sets"], s["sets"]),
            lambda s: s["weak_sets"] == 0),
           ("Weak Hits",
            lambda s: ("ratio", s["weak_hits"], s["hits"]),
            lambda s: s["weak_hits"] == 0),
    ]

def _core_stat_label_html(label: str) -> str:
    definition = CORE_STAT_DEFINITIONS.get(label)
    if not definition:
        return _esc(label)
    return (
        f'{_esc(label)} '
        f'<span class="stat-help" title="{_esc(definition)}">ⓘ</span>'
    )


# --------------------------------------------------------------------- #
#  Top-level entry point
# --------------------------------------------------------------------- #


def render_analysis_html(
    stats: dict,
    *,
    flow_strip_png_b64: Optional[str] = None,
    flow_chart_png_b64: Optional[str] = None,
    per_game: Optional[list[dict]] = None,
) -> str:
    """Build a complete, self-contained HTML document for a stats dict.

    `stats` is whatever `compute_game_stats` or `compute_match_stats`
    returns — same shape as the analysis screen consumes. The optional
    `flow_*_png_b64` arguments are base64-encoded PNGs of the bitmap
    flow widgets (caller responsibility); when omitted those sections
    are skipped without breaking the document.

    When the document is a match aggregate, pass `per_game` as a list of
    `{"stats": <per-game stats dict>, "flow_strip_png_b64": ...,
       "flow_chart_png_b64": ..., "label": "Game 1  21–15"}` entries —
    one per game in chronological order. The resulting document then
    includes the match aggregate plus a separate panel for each game,
    with a `[Match] [Game 1] [Game 2] ...` toggle that mirrors the
    in-app match-toggle bar.
    """
    title = _doc_title(stats)
    if per_game:
        body = _render_tabbed_match(
            stats, flow_strip_png_b64, flow_chart_png_b64, per_game
        )
    else:
        body = _render_panel(stats, flow_strip_png_b64, flow_chart_png_b64)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>{_css()}</style>
</head>
<body>
<main class="container">
{body}
<footer>Exported {_esc(datetime.now().strftime("%Y-%m-%d %H:%M"))} · Roundnet Stats Tracker</footer>
</main>
{_TAB_SCRIPT if per_game else ""}
</body>
</html>
"""


def render_player_core_stats_html(stats: dict) -> str:
    """Build a compact self-contained HTML document with only the
    per-player Core stats table."""
    title = f"{_doc_title(stats)} · Core stats (per player)"
    body = (
        _render_header(stats, None)
        + '<section class="card">'
        + '<h2>Core stats — per player</h2>'
        + _render_core_stats_table(
            "Per player", _columns_player(stats), include_help=True
        )
        + '</section>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>{_css()}</style>
</head>
<body>
<main class="container">
{body}
<footer>Exported {_esc(datetime.now().strftime("%Y-%m-%d %H:%M"))} · Roundnet Stats Tracker</footer>
</main>
</body>
</html>
"""


def _render_panel(
    stats: dict,
    flow_strip_png_b64: Optional[str],
    flow_chart_png_b64: Optional[str],
) -> str:
    """The body sections for a single stats payload — header through
    chain analysis. Reused by the single-game export and by each tab
    panel in the match export."""
    return "".join([
        _render_header(stats, flow_strip_png_b64),
        _render_flow(stats, flow_chart_png_b64),
        _render_roundx(stats),
        _render_core_stats(stats),
        _render_faults(stats),
        _render_chains(stats),
    ])


def _render_tabbed_match(
    match_stats: dict,
    match_strip_b64: Optional[str],
    match_chart_b64: Optional[str],
    per_game: list[dict],
) -> str:
    """Tab nav + panels for a match export.

    First tab is the match aggregate. Each subsequent tab is one game in
    the order supplied. Panels live in the same `.tab-panel` class; the
    inline `<script>` at the bottom of the document handles switching.
    """
    g = match_stats["game"]
    a_wins, b_wins = g.get("series_score", (0, 0))
    match_label = f"Match  {a_wins}–{b_wins}"

    tabs_html = [
        f'<button class="active" data-tab="panel-match" type="button">'
        f'{_esc(match_label)}</button>'
    ]
    panels_html = [
        f'<article class="tab-panel" data-tab="panel-match">'
        f'{_render_panel(match_stats, match_strip_b64, match_chart_b64)}'
        f'</article>'
    ]

    for i, entry in enumerate(per_game, start=1):
        gstats = entry["stats"]
        tab_id = f"panel-game-{gstats['game']['id']}"
        label = entry.get("label") or f"Game {i}"
        tabs_html.append(
            f'<button data-tab="{_esc(tab_id)}" type="button">'
            f'{_esc(label)}</button>'
        )
        panels_html.append(
            f'<article class="tab-panel" data-tab="{_esc(tab_id)}" hidden>'
            f'{_render_panel(gstats, entry.get("flow_strip_png_b64"), entry.get("flow_chart_png_b64"))}'
            f'</article>'
        )

    return (
        '<nav class="match-tabs">'
        '<span class="match-tabs-label">Viewing:</span>'
        + "".join(tabs_html) +
        '</nav>'
        + "".join(panels_html)
    )


# Tiny no-dependencies tab switcher. Activated only when `per_game` is
# present (match export); single-game exports omit the script entirely.
_TAB_SCRIPT = """<script>
(function () {
  var buttons = document.querySelectorAll('.match-tabs button');
  var panels = document.querySelectorAll('.tab-panel');
  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var target = btn.getAttribute('data-tab');
      buttons.forEach(function (b) { b.classList.toggle('active', b === btn); });
      panels.forEach(function (p) {
        p.hidden = (p.getAttribute('data-tab') !== target);
      });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });
})();
</script>"""


def _doc_title(stats: dict) -> str:
    g = stats["game"]
    if g.get("is_match"):
        a, b = g.get("series_score", (0, 0))
        return (
            f"Match · {' & '.join(g['team_a_players'])} vs "
            f"{' & '.join(g['team_b_players'])} · {a}-{b}"
        )
    return (
        f"Game #{g['id']} · {' & '.join(g['team_a_players'])} vs "
        f"{' & '.join(g['team_b_players'])} · "
        f"{g['final_score_a']}-{g['final_score_b']}"
    )


# --------------------------------------------------------------------- #
#  Sections
# --------------------------------------------------------------------- #


def _render_header(stats: dict, flow_strip_png_b64: Optional[str]) -> str:
    g = stats["game"]
    is_match = bool(g.get("is_match"))
    team_a = " & ".join(g["team_a_players"])
    team_b = " & ".join(g["team_b_players"])

    if is_match:
        n_games = len(g["game_results"])
        title = (
            f"Match — {n_games} games · starting "
            f"{_fmt_date(g['created_at'])}"
        )
        title_color = PALETTE["match_edge"]
        a_wins, b_wins = g.get("series_score", (0, 0))
        per_game_bits = []
        for i, gr in enumerate(g["game_results"], start=1):
            w = gr["winner"]
            ac = (PALETTE["winner"] if w == "A"
                  else (PALETTE["dim2"] if w == "B" else PALETTE["text"]))
            bc = (PALETTE["winner"] if w == "B"
                  else (PALETTE["dim2"] if w == "A" else PALETTE["text"]))
            per_game_bits.append(
                f'<span class="dim">G{i}</span> '
                f'<b style="color:{ac}">{gr["final_score_a"]}</b>'
                f'<span class="dim">-</span>'
                f'<b style="color:{bc}">{gr["final_score_b"]}</b>'
            )
        score_block = (
            f'<div class="sub">'
            f'<b>{_esc(team_a)}</b> vs <b>{_esc(team_b)}</b><br>'
            f'<b>Series:</b> {a_wins} – {b_wins} '
            f'({_winner_text(g, team_a, team_b)}, '
            f'{g["total_points"]} points across {n_games} games)'
            f'<br><span class="dim2 small">'
            f'{" &nbsp;·&nbsp; ".join(per_game_bits)}</span>'
            f'</div>'
        )
    else:
        title = f"Game #{g['id']} — {_fmt_date(g['created_at'])}"
        title_color = PALETTE["summary"]
        score_block = (
            f'<div class="sub">'
            f'<b>{_esc(team_a)}</b> vs <b>{_esc(team_b)}</b><br>'
            f'<b>Final score:</b> '
            f'{g["final_score_a"]} – {g["final_score_b"]} '
            f'({_winner_text(g, team_a, team_b)}, '
            f'{g["total_points"]} points played)'
            f'</div>'
        )

    strip_img = ""
    if flow_strip_png_b64:
        strip_img = (
            f'<img class="flow-strip" alt="Game flow strip" '
            f'src="data:image/png;base64,{flow_strip_png_b64}">'
        )

    edge_class = " match-edge" if is_match else ""
    return (
        f'<section class="game-header{edge_class}">'
        f'<h1 style="color:{title_color}">{_esc(title)}</h1>'
        f'{score_block}'
        f'{strip_img}'
        f'</section>'
    )


def _winner_text(g: dict, team_a: str, team_b: str) -> str:
    w = g.get("winner")
    if w == "A":
        return f"{_esc(team_a)} won"
    if w == "B":
        return f"{_esc(team_b)} won"
    return "No declared winner"


def _render_flow(stats: dict, flow_chart_png_b64: Optional[str]) -> str:
    if not flow_chart_png_b64 or not stats.get("flow"):
        return ""
    g = stats["game"]
    team_a = " & ".join(g["team_a_players"])
    team_b = " & ".join(g["team_b_players"])
    return (
        '<section class="card">'
        '<h2>Game Flow</h2>'
        '<p class="intro">'
        'Cumulative score per team across every point. Filled '
        '<b>circles</b> mark <span style="color:#ffd700">breaks</span> — '
        'points where the serving team scored. The shaded band between '
        'the lines is tinted in the leader\'s colour, so runs of one '
        'team show as a stretch of their colour.'
        '</p>'
        f'<p class="legend">'
        f'<span style="color:{PALETTE["team_a"]}">■</span> {_esc(team_a)}'
        f' &nbsp; <span style="color:{PALETTE["team_b"]}">■</span> {_esc(team_b)}'
        f'</p>'
        f'<img class="flow-chart" alt="Game flow chart" '
        f'src="data:image/png;base64,{flow_chart_png_b64}">'
        '</section>'
    )


def _render_roundx(stats: dict) -> str:
    roundx = stats.get("roundx") or {}
    if not roundx:
        return ""
    sample = next(iter(roundx.values()), {})
    rated = sample.get("rated", True)
    rallies = sample.get("counted_rallies", 0)
    scalar = sample.get("scalar", 1.0)

    sub_parts = [f"{rallies} rallies", f"scaling ×{scalar:.2f}"]
    if not rated:
        sub_parts.append(
            f'<span style="color:#ff9090">unrated '
            f'(&lt; {rallies or 20} rallies)</span>'
        )
    sub_parts.append(
        '<span class="dim2">· benchmark is a 40-rally game '
        '(typical 21-point match)</span>'
    )
    sub = " · ".join(sub_parts)

    names = stats["game"]["names"]
    cards = "".join(
        _render_roundx_card(slot, names[slot], roundx.get(slot))
        for slot in ("A1", "A2", "B1", "B2")
    )
    return (
        '<section class="card">'
        '<h2>RoundX — Match Impact</h2>'
        f'<p class="intro">{sub}</p>'
        f'<div class="roundx-grid">{cards}</div>'
        '</section>'
    )


def _render_roundx_card(slot: str, name: str, d: Optional[dict]) -> str:
    head = (
        f'<div class="roundx-head">'
        f'<b>{_esc(slot)}</b> {_esc(name)}'
        f'</div>'
    )
    if not d:
        return (
            f'<div class="roundx-card">'
            f'{head}<div class="roundx-score dim">—</div>'
            f'</div>'
        )

    score = d.get("score", 0)
    rel = d.get("relative_to_avg", 0)
    score_color = (PALETTE["pos"] if rel > 0
                   else (PALETTE["neg"] if rel < 0 else PALETTE["hot"]))
    sign = "+" if score >= 0 else ""
    rel_sign = "+" if rel >= 0 else ""

    breakdown = d.get("breakdown") or []
    pos_rows, neg_rows = _aggregate_breakdown(breakdown)
    pos_html = ""
    if pos_rows:
        pos_html = (
            f'<div class="bd-head" style="color:{PALETTE["pos"]}">Boosted score</div>'
            + "".join(_render_bd_row(lbl, count, total, True)
                      for lbl, count, total in pos_rows)
        )
    neg_html = ""
    if neg_rows:
        neg_html = (
            f'<div class="bd-head" style="color:{PALETTE["neg"]}">Hurt score</div>'
            + "".join(_render_bd_row(lbl, count, total, False)
                      for lbl, count, total in neg_rows)
        )
    if not (pos_rows or neg_rows):
        empty = '<div class="empty">No scored events</div>'
    else:
        empty = ""

    return (
        f'<div class="roundx-card">'
        f'{head}'
        f'<div class="roundx-score" style="color:{score_color}">{sign}{score}</div>'
        f'<div class="roundx-rel">({rel_sign}{rel} vs game avg)</div>'
        f'<hr>'
        f'{pos_html}{neg_html}{empty}'
        f'</div>'
    )


def _aggregate_breakdown(
    breakdown: list[tuple[str, int]],
) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """Duplicate of analysis_screen._aggregate_breakdown — sums per-label
    counts and totals, splits positives/negatives. See that function for
    the rationale."""
    from collections import defaultdict
    counts: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    for lbl, delta in breakdown:
        counts[lbl] += 1
        totals[lbl] += delta
    pos: list[tuple[str, int, int]] = []
    neg: list[tuple[str, int, int]] = []
    for lbl in counts:
        total = totals[lbl]
        if total >= 0:
            pos.append((lbl, counts[lbl], total))
        else:
            neg.append((lbl, counts[lbl], total))
    pos.sort(key=lambda x: -x[2])
    neg.sort(key=lambda x: x[2])
    return pos, neg


def _render_bd_row(label: str, count: int, total: int, positive: bool) -> str:
    text = label if count == 1 else f"{label} ×{count}"
    sign = "+" if total >= 0 else ""
    color = PALETTE["pos"] if positive else PALETTE["neg"]
    return (
        f'<div class="bd-row">'
        f'<span>{_esc(text)}</span>'
        f'<b style="color:{color}">{sign}{total}</b>'
        f'</div>'
    )


# --------------------------------------------------------------------- #
#  Core stats / faults / chain tables
# --------------------------------------------------------------------- #


def _columns_team(stats: dict) -> list[tuple[str, dict]]:
    g = stats["game"]
    return [
        (" & ".join(g["team_a_players"]), stats["teams"]["A"]),
        (" & ".join(g["team_b_players"]), stats["teams"]["B"]),
    ]


def _columns_player(stats: dict) -> list[tuple[str, dict]]:
    names = stats["game"]["names"]
    return [
        (f"{names[slot]}  ({slot})", stats["players"][slot])
        for slot in ("A1", "A2", "B1", "B2")
    ]


def _render_core_stats(stats: dict) -> str:
    return (
        '<section class="card">'
        '<h2>Core stats</h2>'
        + _render_core_stats_table(
            "Per team", _columns_team(stats), include_help=True
        )
        + _render_core_stats_table(
            "Per player", _columns_player(stats), include_help=True
        )
        + '</section>'
    )


def _render_core_stats_table(
    sub_label: str, columns: list[tuple[str, dict]], include_help: bool = True,
) -> str:
    rows = _stat_rows()
    header_cells = "".join(f"<th>{_esc(c[0])}</th>" for c in columns)
    body = []
    for label, getter, is_zero in rows:
        cells = []
        for _name, src in columns:
            zero = is_zero(src)
            color = PALETTE["dim"] if zero else PALETTE["hot"]
            weight = "normal" if zero else "bold"
            val = getter(src)
            if val[0] == "pair":
                cell_html = f"{val[1]}/{val[2]}"
            elif val[0] == "ratio":
                main, sub = _ratio_parts(val[1], val[2])
                cell_html = f'{main}<br><span class="sub">{sub}</span>'
            else:  # plain
                cell_html = str(val[1])
            cells.append(
                f'<td style="color:{color}; font-weight:{weight}">{cell_html}</td>'
            )
        label_html = _core_stat_label_html(label) if include_help else _esc(label)
        body.append(f'<tr><td class="label">{label_html}</td>{"".join(cells)}</tr>')
    return (
        f'<h3>{_esc(sub_label)}</h3>'
        '<table class="stats-table">'
        f'<thead><tr><th>Stat</th>{header_cells}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody>'
        '</table>'
    )


def _render_faults(stats: dict) -> str:
    return (
        '<section class="card">'
        '<h2>Service faults by type</h2>'
        + _render_fault_table("Per team", _columns_team(stats))
        + _render_fault_table("Per player", _columns_player(stats))
        + '</section>'
    )


def _render_fault_table(
    sub_label: str, columns: list[tuple[str, dict]],
) -> str:
    fault_types = list(FAULT_TYPES)
    for _name, src in columns:
        for k in src.get("all_faults_by_type", {}):
            if k not in fault_types:
                fault_types.append(k)

    SUMMARY = ("Double Faults", "First Faults")
    row_labels = fault_types + list(SUMMARY)

    header_cells = "".join(f"<th>{_esc(c[0])}</th>" for c in columns)
    rows = []
    for label in row_labels:
        is_summary = label in SUMMARY
        lbl_color = PALETTE["summary"] if is_summary else PALETTE["label"]
        lbl_weight = "bold" if is_summary else "normal"
        cells = []
        for _name, src in columns:
            if label == "Double Faults":
                value = src["double_faults_total"]
            elif label == "First Faults":
                value = src["single_faults_total"]
            else:
                value = src["all_faults_by_type"].get(label, 0)
            color = PALETTE["dim"] if value == 0 else _heat_color(value)
            weight = "bold" if (is_summary or value != 0) else "normal"
            cells.append(
                f'<td style="color:{color}; font-weight:{weight}">{value}</td>'
            )
        rows.append(
            f'<tr><td class="label" '
            f'style="color:{lbl_color}; font-weight:{lbl_weight}">'
            f'{_esc(label)}</td>{"".join(cells)}</tr>'
        )
    return (
        f'<h3>{_esc(sub_label)}</h3>'
        '<table class="stats-table">'
        f'<thead><tr><th>Fault type</th>{header_cells}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
    )


def _render_chains(stats: dict) -> str:
    chains = stats.get("chains") or {}
    if not chains:
        return ""

    intro = (
        'Each cell shows the <b>loss rate</b> when the pattern occurred, '
        'with the point count below. '
        '<span style="color:#ff7070">Red</span> = above team baseline '
        '(weak action correlates with losing). '
        '<span style="color:#7adb7a">Green</span> = below baseline. '
        'Dim grey = pattern didn\'t occur.'
    )
    return (
        '<section class="card">'
        '<h2>Chain analysis — weak actions and point loss</h2>'
        f'<p class="intro">{intro}</p>'
        + _render_chain_subview("Per team", _chain_subjects_team(stats))
        + _render_chain_subview("Per player", _chain_subjects_player(stats))
        + '</section>'
    )


def _chain_subjects_team(
    stats: dict,
) -> list[tuple[str, float, list[dict]]]:
    g = stats["game"]
    chains = stats["chains"]
    return [
        (" & ".join(g["team_a_players"]),
         chains["A"]["baseline_loss_rate"],
         chains["A"]["team_patterns"]),
        (" & ".join(g["team_b_players"]),
         chains["B"]["baseline_loss_rate"],
         chains["B"]["team_patterns"]),
    ]


def _chain_subjects_player(
    stats: dict,
) -> list[tuple[str, float, list[dict]]]:
    g = stats["game"]
    names = g["names"]
    chains = stats["chains"]
    out: list[tuple[str, float, list[dict]]] = []
    for team in ("A", "B"):
        base = chains[team]["baseline_loss_rate"]
        for slot in (f"{team}1", f"{team}2"):
            pp = chains[team]["per_player"][slot]
            out.append((f"{names[slot]}  ({slot})", base, pp["patterns"]))
    return out


def _render_chain_subview(
    sub_label: str, subjects: list[tuple[str, float, list[dict]]],
) -> str:
    if not subjects:
        return f"<h3>{_esc(sub_label)}</h3><p>No data.</p>"

    baseline_parts = [
        f'<b style="color:{PALETTE["summary"]}">{_esc(name)}</b> '
        f'<span style="color:{PALETTE["hot"]}">{_fmt_pct(base)}</span>'
        for (name, base, _) in subjects
    ]
    baseline_line = (
        f'<div class="baseline-strip">'
        f'Baseline loss rate · {"  |  ".join(baseline_parts)}'
        f'</div>'
    )

    pattern_labels = [p["pattern"] for p in subjects[0][2]]
    header_cells = "".join(f"<th>{_esc(s[0])}</th>" for s in subjects)

    rows = []
    for pat_label in pattern_labels:
        cells = []
        for _name, _base, patterns in subjects:
            pat = next((p for p in patterns if p["pattern"] == pat_label), None)
            cells.append(_render_chain_cell(pat))
        rows.append(
            f'<tr><td class="label">{_esc(pat_label)}</td>{"".join(cells)}</tr>'
        )

    return (
        f'<h3>{_esc(sub_label)}</h3>'
        f'{baseline_line}'
        '<table class="stats-table">'
        f'<thead><tr><th>Pattern</th>{header_cells}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
    )


def _render_chain_cell(pat: Optional[dict]) -> str:
    if pat is None or pat["count"] == 0:
        return f'<td style="color:{PALETTE["dim"]}">—</td>'
    rate = _fmt_pct(pat["loss_rate"])
    count = pat["count"]
    vs = pat["vs_baseline"]
    vs_suffix = ""
    if vs is not None and abs(vs) > 0.001:
        vs_suffix = f"  {_fmt_signed_pp(vs)}"
    color = _color_for_vs_baseline(vs)
    return (
        f'<td style="color:{color}; font-weight:bold">'
        f'{_esc(rate)}<br><span class="sub">n={count}{_esc(vs_suffix)}</span>'
        f'</td>'
    )


# --------------------------------------------------------------------- #
#  Styles
# --------------------------------------------------------------------- #


def _css() -> str:
    p = PALETTE
    return f"""
* {{ box-sizing: border-box; }}
html, body {{
    margin: 0; padding: 0;
    background: {p["bg"]};
    color: {p["text"]};
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
    line-height: 1.4;
}}
.container {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px 24px 48px;
}}
h1 {{ font-size: 22px; margin: 0 0 8px; }}
h2 {{
    font-size: 16px; margin: 0 0 10px;
    color: {p["summary"]};
}}
h3 {{
    font-size: 13px; margin: 14px 0 6px;
    color: {p["label"]}; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.5px;
}}
.dim   {{ color: {p["dim"]}; }}
.dim2  {{ color: {p["dim2"]}; }}
.small {{ font-size: 12px; }}
.sub   {{ color: {p["text"]}; }}
.intro {{
    color: {p["label"]}; font-size: 12px; margin: 0 0 10px;
}}
.legend {{ color: {p["label"]}; font-size: 12px; margin: 0 0 10px; }}

footer {{
    color: {p["dim"]}; font-size: 11px;
    text-align: center; margin-top: 24px;
}}

/* Sections */
.card, .game-header {{
    background: {p["panel"]};
    border: 1px solid {p["border"]};
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 14px;
}}
.match-edge {{ border-color: {p["match_edge"]}; }}

.flow-strip {{
    display: block; width: 100%; margin-top: 8px;
    border-radius: 6px;
    background: {p["panel_alt"]};
}}
.flow-chart {{
    display: block; width: 100%;
    margin-top: 6px;
    background: {p["panel_alt"]};
    border-radius: 6px;
}}

/* RoundX cards */
.roundx-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 10px;
}}
.roundx-card {{
    background: {p["panel2"]};
    border: 1px solid {p["border"]};
    border-radius: 8px;
    padding: 12px;
}}
.roundx-head {{
    color: {p["summary"]}; font-size: 13px;
    margin-bottom: 4px;
}}
.roundx-head b {{ color: {p["summary"]}; }}
.roundx-score {{
    font-size: 30px; font-weight: bold;
    text-align: center; margin: 4px 0;
}}
.roundx-rel {{
    color: {p["label"]}; font-size: 11px;
    text-align: center; margin-bottom: 6px;
}}
.roundx-card hr {{
    border: 0; border-top: 1px solid {p["border"]}; margin: 6px 0;
}}
.bd-head {{
    font-size: 11px; font-weight: bold;
    text-transform: uppercase; letter-spacing: 0.5px;
    margin: 4px 0 2px;
}}
.bd-row {{
    display: flex; justify-content: space-between;
    font-size: 12px; padding: 1px 0;
    color: #ddddee;
}}
.empty {{ color: {p["dim"]}; font-size: 11px; text-align: center; }}

/* Tables */
.stats-table {{
    width: 100%;
    border-collapse: collapse;
    background: {p["panel"]};
    margin-bottom: 8px;
}}
.stats-table th, .stats-table td {{
    padding: 6px 10px;
    text-align: center;
    border-bottom: 1px solid {p["sub_grid"]};
    vertical-align: middle;
}}
.stats-table th {{
    background: {p["header_bg"]};
    color: {p["summary"]};
    font-weight: bold;
    font-size: 12px;
}}
.stats-table tbody tr:nth-child(odd) td {{
    background: {p["panel2"]};
}}
.stats-table td.label {{
    color: {p["label"]};
    text-align: left;
    font-weight: 500;
}}
.stats-table td.label .stat-help {{
    margin-left: 4px;
    color: {p["summary"]};
    font-weight: 600;
    font-size: 11px;
    cursor: help;
}}
.stats-table .sub {{
    color: {p["dim2"]}; font-size: 11px;
    display: inline-block; margin-top: 1px;
}}

/* Chain baseline strip */
.baseline-strip {{
    color: {p["label"]}; font-size: 12px;
    padding: 6px 10px;
    background: {p["panel_alt"]};
    border-radius: 6px;
    margin: 4px 0 8px;
}}

/* Match-export tab nav — mirrors the in-app toggle bar */
.match-tabs {{
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
    padding: 10px 12px;
    background: {p["panel"]};
    border: 1px solid {p["match_edge"]};
    border-radius: 6px;
    margin-bottom: 14px;
    position: sticky; top: 0; z-index: 10;
}}
.match-tabs-label {{
    color: {p["label"]}; font-size: 13px;
    margin-right: 4px;
}}
.match-tabs button {{
    background: #2d2d4e;
    color: {p["label"]};
    border: 1px solid #444466;
    border-radius: 4px;
    padding: 6px 14px;
    cursor: pointer;
    font: inherit;
    font-size: 13px;
}}
.match-tabs button:hover {{ border-color: #aabbff; }}
.match-tabs button.active {{
    background: {p["match_edge"]};
    color: {p["panel"]};
    border-color: {p["match_edge"]};
    font-weight: bold;
}}

.tab-panel[hidden] {{ display: none; }}
"""
