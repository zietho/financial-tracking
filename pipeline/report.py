"""Render out/report.html from out/finance.duckdb.

Usage: uv run python -m pipeline.report [data_dir]

One self-contained HTML file (Plotly inlined, works offline). Light and dark
mode follow the OS; colors are the validated dataviz reference palette.
"""

from __future__ import annotations

import html
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

from .build import data_dir

# ---------------------------------------------------------------- palette
LIGHT = dict(paper="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781", grid="#e1e0d9", axis="#c3c2b7",
             series=["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
             seq=["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#104281"])
DARK = dict(paper="#1a1a19", ink="#f0efec", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a", axis="#383835",
            series=["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"],
            seq=["#1f2c3d", "#104281", "#1c5cab", "#3987e5", "#86b6ef"])
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = range(8)


def scale(colors):
    return [[i / (len(colors) - 1), c] for i, c in enumerate(colors)]


def eur(x, decimals=0):
    if x is None:
        return "–"
    return f"€{x:,.{decimals}f}".replace("€-", "−€")


def pct(x):
    return "–" if x is None else f"{x * 100:.0f}%"


def base_layout(fig: go.Figure, height=320, **kw) -> go.Figure:
    t = LIGHT
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor=t["paper"], plot_bgcolor=t["paper"],
        font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', size=12, color=t["ink2"]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None),
        hoverlabel=dict(font_family="system-ui, -apple-system, sans-serif"),
        bargap=0.25, bargroupgap=0.08, **kw)
    fig.update_xaxes(showgrid=False, linecolor=t["axis"], ticks="", zeroline=False)
    fig.update_yaxes(gridcolor=t["grid"], gridwidth=1, zeroline=False, linecolor=t["axis"])
    return fig


class Report:
    def __init__(self, data: Path):
        self.data = data
        self.con = duckdb.connect(str(data / "out" / "finance.duckdb"), read_only=True)
        self.figs: list[str] = []
        max_date = self.one("SELECT max(date) FROM tx")
        month_end = (max_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        m = max_date.replace(day=1)
        # report on the latest complete-ish month
        self.month = m if (month_end - max_date).days <= 5 else (m - timedelta(days=1)).replace(day=1)
        self.max_date = max_date
        self.latest = self.one("SELECT max(month) FROM net_worth") or self.month
        self.start12 = (self.month.replace(day=15) - timedelta(days=330)).replace(day=1)

    def one(self, sql, *p):
        return self.con.execute(sql, p).fetchone()[0]

    def rows(self, sql, *p) -> list[dict]:
        cur = self.con.execute(sql, p)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def fig(self, fig: go.Figure) -> str:
        div = fig.to_html(full_html=False, include_plotlyjs=False,
                          config={"displayModeBar": False, "responsive": True})
        return f'<div class="plot">{div}</div>'

    # ------------------------------------------------------------ sections
    def kpis(self) -> str:
        nw = self.rows("SELECT month, net_worth FROM net_worth ORDER BY month")
        nw_now = nw[-1]["net_worth"] if nw else None
        nw_base = next((i for i in nw if i["month"] >= self.latest - timedelta(days=360)), nw[0] if nw else None)
        k = self.rows("SELECT * FROM monthly_kpis WHERE month = ?", self.month)
        k = k[0] if k else {}
        spent = float(self.one("""SELECT -sum(amount) FROM tx_enriched WHERE kind = 'expense'
                                  AND month BETWEEN ? AND ?""", self.start12, self.month) or 0)
        n_odd = self.one("SELECT count(*) FROM unusual WHERE month BETWEEN ? AND ?", self.start12, self.month)
        income = float(self.one("""SELECT sum(amount) FROM tx_enriched WHERE kind = 'income'
                                   AND month BETWEEN ? AND ?""", self.start12, self.month) or 0)

        def tile(label, value, sub, href):
            return (f'<a class="tile" href="{href}"><div class="tile-label">{label}</div>'
                    f'<div class="tile-value">{value}</div><div class="tile-sub">{sub}</div></a>')

        sign = lambda x: ("+" if x >= 0 else "−") + eur(abs(x))
        return "".join([
            tile("Net worth", eur(nw_now),
                 f"{self.latest:%B %Y} · {nw[-2]['month']:%B}: {eur(nw[-2]['net_worth'])}" if len(nw) > 1 else f"{self.latest:%B %Y}",
                 "#networth"),
            tile("Income", eur(income / 12), f"per month, last 12 months · {self.month:%B}: {eur(k.get('income'))}", "#time"),
            tile("Spending", eur(spent / 12),
                 f"per month, last 12 months · {self.month:%B}: {eur(k.get('expenses'))}", "#where"),
            tile("Left over", eur((income - spent) / 12),
                 f"{pct((income - spent) / income) if income else '–'} of income · {self.month:%B}: "
                 f"{eur(k.get('saved'))} ({pct(k.get('savings_rate'))})", "#networth"),
            tile("Worth a look", str(n_odd), "unusual or one-off expenses", "#unusual"),
        ])

    def nw_mix(self) -> str:
        r = self.rows("SELECT * FROM net_worth WHERE month = ?", self.latest)
        if not r:
            return ""
        r = r[0]
        parts = [(n, r[k]) for n, k in (("cash", "cash"), ("reserved", "reserved"), ("stocks & ETFs", "investments"),
                                        ("property", "property"), ("debt", "debt")) if r[k]]
        return f"{self.latest:%b %Y}: " + " · ".join(f"{n} {eur(v / 1000)}k".replace(".0k", "k") for n, v in parts)


    def after_move(self) -> str:
        """private/plan.csv (gitignored): housing costs today vs. after moving into our own place."""
        path = self.data / "private" / "plan.csv"
        if not path.exists():
            return ""
        import csv as _csv
        v = {r["key"]: r["value"] for r in _csv.DictReader(path.open())}
        f = lambda k: float(v.get(k) or 0)
        now = f("rent_now") + f("running_now")
        after = f("installments") + f("loan_insurance") + f("running_after")
        cash_income = float(self.one("""SELECT sum(amount) FROM tx_enriched WHERE kind = 'income' AND month BETWEEN ? AND ?
                                        AND category NOT IN ('Stock compensation', 'Pension via payroll')""", self.start12, self.month) or 0)
        spent = float(self.one("""SELECT -sum(amount) FROM tx_enriched WHERE kind = 'expense' AND month BETWEEN ? AND ?
                                  AND category <> 'Loan fees'""", self.start12, self.month) or 0)
        saving_now = (cash_income - spent) / 12
        rows = [("Rent incl. operating costs + parking", f("rent_now"), 0), ("Loan installments", 0, f("installments")),
                ("Loan life cover", 0, f("loan_insurance")),
                ("Operating costs, electricity, heating, subscriptions", f("running_now"), f("running_after"))]
        body = "".join(f"<tr><td>{n}</td><td class=num>{eur(a) if a else '–'}</td><td class=num>{eur(b) if b else '–'}</td></tr>"
                       for n, a, b in rows)
        body += (f"<tr><th>Housing per month</th><th class=num>{eur(now)}</th><th class=num>{eur(after)}</th></tr>"
                 f"<tr><td>Cash saving per month (before RSU)</td><td class=num>{eur(saving_now)}</td>"
                 f"<td class=num>{eur(saving_now - (after - now))}</td></tr>")
        ratio = after / (cash_income / 12) if cash_income else 0
        return (f'<section class="card" id="move"><h2>What changes when we move ({html.escape(v.get("move_from", ""))})?</h2>'
                f'<p class="takeaway">Housing goes up by <b>{eur(after - now)}</b> a month to {eur(after)}, '
                f'{ratio:.0%} of cash income.</p>'
                f'<div class="scroll"><table><thead><tr><th></th><th class=num>Today</th><th class=num>After the move</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div></section>')

    def income_by_person(self, start: date, end: date) -> list[dict]:
        """Income per category and earner. earners.csv (pattern,person; gitignored) names who a payment is from,
        e.g. the employer; otherwise the account owner, unless the account is shared."""
        import csv as _csv
        path = self.data / "earners.csv"
        earners = [(re.compile(r["pattern"], re.I), r["person"]) for r in _csv.DictReader(path.open())] if path.exists() else []
        rows = self.rows("""SELECT category, coalesce(payee, '') || ' ' || coalesce(memo, '') AS text, owner, amount
                             FROM tx_enriched WHERE kind = 'income' AND month BETWEEN ? AND ?""", start, end)
        out: dict[str, float] = {}
        for r in rows:
            person = next((p for rx, p in earners if rx.search(r["text"])), None)
            if not person and r["owner"] and r["owner"].lower() not in ("both", "?"):
                person = r["owner"]
            key = f"{r['category']} · {person}" if person else r["category"]
            out[key] = out.get(key, 0) + float(r["amount"])
        return [{"category": k, "v": v} for k, v in sorted(out.items(), key=lambda x: -x[1]) if v > 0]

    def flows(self, start: date, end: date) -> str:
        """Sankey: income by category -> household -> spending groups -> categories, plus what is left over."""
        inc = self.income_by_person(start, end)
        exp = self.rows("""SELECT "group" grp, category, -sum(amount) v FROM tx_enriched WHERE kind = 'expense'
                           AND month BETWEEN ? AND ? GROUP BY ALL HAVING -sum(amount) > 0 ORDER BY v DESC""", start, end)
        if not inc or not exp:
            return '<p class="note">Not enough data for this period.</p>'
        groups = {}
        for e in exp:
            groups[e["grp"]] = groups.get(e["grp"], 0) + float(e["v"])
        order = [g for g, _ in sorted(groups.items(), key=lambda x: -x[1])]
        gslot = {g: (i if i < 7 else -1) for i, g in enumerate(order)}
        labels, slots, idx = [], [], {}

        def node(key, label, slot):
            if key not in idx:
                idx[key] = len(labels); labels.append(label); slots.append(slot)
            return idx[key]

        total_in = sum(float(i["v"]) for i in inc)
        total_out = sum(groups.values())
        src, tgt, val, col = [], [], [], []

        def link(a, b, v, color_node):
            src.append(a); tgt.append(b); val.append(v); col.append(color_node)

        hub = node("hub", f"Household {eur(total_in)}", -1)
        for i in inc:
            n = node(("in", i["category"]), i["category"], BLUE)
            link(n, hub, float(i["v"]), n)
        for g in order:
            gn = node(("g", g), f"{g} {eur(groups[g])}", gslot[g])
            link(hub, gn, groups[g], gn)
        for e in exp:
            cn = node(("c", e["category"]), e["category"], gslot[e["grp"]])
            link(idx[("g", e["grp"])], cn, float(e["v"]), cn)
        if total_in > total_out:
            ln = node("left", f"Left over {eur(total_in - total_out)}", AQUA)
            link(hub, ln, total_in - total_out, ln)

        def rgba(hexc, a):
            h = hexc.lstrip("#")
            return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"
        color = lambda sl: LIGHT["muted"] if sl < 0 else LIGHT["series"][sl]
        fig = go.Figure(go.Sankey(
            arrangement="snap", valueformat=",.0f", valuesuffix=" €",
            node=dict(label=labels, color=[color(sl) for sl in slots], pad=10, thickness=12, line=dict(width=0)),
            link=dict(source=src, target=tgt, value=val, color=[rgba(color(slots[c]), 0.3) for c in col]),
            meta={"node_slots": slots, "link_src": col}))
        base_layout(fig, height=max(480, 22 * len(exp) + 80))
        return self.fig(fig)

    def over_time(self) -> str:
        """Spending stacked by group per month, income as a line on the same € axis."""
        r = self.rows("""SELECT month, "group", -sum(amount) s FROM tx_enriched WHERE kind = 'expense'
                         AND month BETWEEN ? AND ? GROUP BY ALL""", self.start12, self.month)
        inc = {i["month"]: float(i["income"]) for i in
               self.rows("SELECT month, income FROM monthly WHERE month BETWEEN ? AND ?", self.start12, self.month)}
        totals: dict[str, float] = {}
        for i in r:
            totals[i["group"]] = totals.get(i["group"], 0) + float(i["s"])
        top = [g for g, _ in sorted(totals.items(), key=lambda x: -x[1]) if g != "Other"][:6]
        months = sorted({i["month"] for i in r} | set(inc))
        x = [f"{m:%b %y}" for m in months]
        fig = go.Figure()
        for slot, g in enumerate(top + ["Other"]):
            ys = [sum(float(i["s"]) for i in r if i["month"] == m and (i["group"] == g if g in top else i["group"] not in top))
                  for m in months]
            other = g not in top
            fig.add_bar(x=x, y=ys, name="Other" if other else g, meta={"muted": True} if other else {"slot": slot},
                        marker_color=LIGHT["muted"] if other else LIGHT["series"][slot], hovertemplate="%{y:,.0f} €")
        fig.add_scatter(x=x, y=[inc.get(m, 0) for m in months], name="Income", mode="lines+markers",
                        line=dict(width=2, color=LIGHT["ink"]), marker=dict(size=8), meta={"ink": "ink"},
                        hovertemplate="%{y:,.0f} €")
        base_layout(fig, barmode="stack", hovermode="x unified", barcornerradius=4, height=380)
        fig.update_traces(marker_line_width=2, marker_line_color=LIGHT["paper"], selector=dict(type="bar"))
        fig.update_yaxes(tickprefix="€", tickformat=",.0f")
        fig.update_xaxes(type="category", tickangle=0, nticks=7)
        return self.fig(fig)

    def where(self) -> tuple[str, str]:
        """Categories over 12 months, variable in color and fixed in gray, plus a one-line takeaway."""
        r = self.rows("""
            SELECT category, any_value(fixed) fixed, -sum(amount) total,
                   -sum(amount) FILTER (month > ? - INTERVAL 3 MONTH) / 3 recent,
                   -sum(amount) FILTER (month <= ? - INTERVAL 3 MONTH) / 9 earlier
            FROM tx_enriched WHERE kind = 'expense' AND month BETWEEN ? AND ?
            GROUP BY 1 HAVING -sum(amount) > 0 ORDER BY total DESC""", self.month, self.month, self.start12, self.month)
        shown = list(reversed(r))  # every category; folding the small ones hid €18k

        def trend(i):
            if not i["earlier"] or float(i["earlier"]) < 20:
                return ""
            ch = float(i["recent"] or 0) / float(i["earlier"]) - 1
            return f"<br>last 3 months {ch:+.0%} vs. before"

        fig = go.Figure()
        kind = lambda i: "Not categorized yet" if i["category"] == "Uncategorized" else ("Fixed" if i["fixed"] == "fixed" else "Variable")
        for name, meta, color, pattern in (("Variable", {"slot": BLUE}, LIGHT["series"][BLUE], ""),
                                           ("Fixed", {"muted": True}, LIGHT["muted"], ""),
                                           ("Not categorized yet", {"muted": True}, LIGHT["muted"], "/")):
            sel = [i for i in shown if kind(i) == name]
            if not sel:
                continue
            fig.add_bar(y=[i["category"] for i in sel], x=[float(i["total"]) for i in sel], orientation="h", name=name,
                        meta=meta, marker_color=color, marker_pattern_shape=pattern,
                        customdata=[[float(i["total"]) / 12, trend(i)] for i in sel],
                        hovertemplate="%{y}: %{x:,.0f} € (%{customdata[0]:,.0f} €/month)%{customdata[1]}<extra></extra>")
        base_layout(fig, height=60 + 28 * len(shown), barcornerradius=4)
        fig.update_yaxes(categoryorder="array", categoryarray=[i["category"] for i in shown], showgrid=False)
        fig.update_xaxes(tickprefix="€", tickformat=",.0f", showgrid=True, gridcolor=LIGHT["grid"], nticks=5)

        var = sum(float(i["total"]) for i in r if i["fixed"] != "fixed" and i["category"] != "Uncategorized")
        top_var = [i for i in r if i["fixed"] != "fixed" and i["category"] != "Uncategorized"][:3]
        rec = float(self.one("SELECT sum(avg_amount) FROM recurring") or 0)
        trips = self.rows("""SELECT trip, -sum(amount) s FROM tx_enriched WHERE kind = 'expense' AND trip IS NOT NULL
                             AND month BETWEEN ? AND ? GROUP BY 1 ORDER BY s DESC""", self.start12, self.month)
        parts = [f"Variable spending was <b>{eur(var)}</b> in 12 months; 10% less would save <b>{eur(var * 0.1)}</b> a year."]
        if top_var:
            parts.append("Largest variable: " + ", ".join(f"{html.escape(i['category'])} {eur(i['total'])}" for i in top_var) + ".")
        if rec:
            parts.append(f"Recurring payments come to about {eur(rec)} a month.")
        if trips:
            parts.append("Vacations: " + ", ".join(f"{html.escape(t['trip'])} {eur(t['s'])}" for t in trips) + ".")
        return self.fig(fig), " ".join(parts)

    def unusual(self) -> str:
        n = self.one("SELECT count(*) FROM unusual WHERE month BETWEEN ? AND ?", self.start12, self.month)
        r = self.rows("""SELECT date, payee, category, spent, reason FROM unusual WHERE month BETWEEN ? AND ?
                         ORDER BY spent DESC LIMIT 6""", self.start12, self.month)
        items = "".join(
            f'<li><span class="amt">{eur(i["spent"])}</span> <b>{html.escape(i["payee"] or "")}</b> '
            f'<span class="dim">{i["date"]:%d %b} · {html.escape(i["reason"])}</span></li>' for i in r)
        more = (f'<p class="note">{n - len(r)} more in the database (view <code>unusual</code>). '
                f'Checked one and it\'s fine? Add its <code>tx_id</code> to <code>reviewed.csv</code> to hide it.</p>' if n > len(r) else "")
        return f'<ul class="list">{items}</ul>{more}' if r else '<p class="note ok">Nothing stands out.</p>'

    def notes(self) -> str:
        """private/learnings.md (gitignored), rendered as simple HTML: ## headings, - bullets, **bold**."""
        path = self.data / "private" / "learnings.md"
        if not path.exists():
            return ""
        out, in_list, first = [], False, True
        inline = lambda t: re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", re.sub(r"`(.+?)`", r"<code>\1</code>", html.escape(t)))
        for line in path.read_text().splitlines():
            line = line.rstrip()
            if line.startswith("# "):
                continue  # the card has its own title
            if first and line and not line.startswith(("## ", "- ")):
                continue  # skip the intro paragraph about the file itself
            if line.startswith(("## ", "- ")):
                first = False
            if line.startswith("- "):
                if not in_list:
                    out.append("<ul class='notes'>"); in_list = True
                out.append(f"<li>{inline(line[2:])}</li>")
                continue
            if in_list:
                out.append("</ul>"); in_list = False
            if line.startswith("## "):
                out.append(f"<h3>{inline(line[3:])}</h3>")
            elif line:
                out.append(f"<p class='note'>{inline(line)}</p>")
        if in_list:
            out.append("</ul>")
        return f'<details class="card" id="notes"><summary>Our notes</summary>{"".join(out)}</details>'


    def data_quality(self) -> str:
        acc = self.rows("""
            SELECT a.name, a.format, a.type, max(t.date) FILTER (t.source_file IS NOT NULL) AS last_tx, count(t.tx_id) FILTER (t.source_file IS NOT NULL) AS n,
                   (SELECT max(b.date) FROM balances b WHERE b.account = a.account) AS last_balance
            FROM accounts a LEFT JOIN tx t USING (account) GROUP BY ALL ORDER BY a.name""")
        lines = []
        for a in acc:
            stale_tx = a["format"] and (a["last_tx"] is None or a["last_tx"] < self.month)
            stale_bal = a["last_balance"] is None or (a["last_balance"] < self.month and a["type"] != "property")
            flag = lambda bad: ' class="warn"' if bad else ""
            lines.append(
                f"<tr><td>{html.escape(a['name'])}</td>"
                f"<td{flag(stale_tx)}>{a['last_tx'] or ('balance only' if not a['format'] else 'no exports')}</td>"
                f"<td class=num>{a['n'] if a['format'] else '–'}</td>"
                f"<td{flag(stale_bal)}>{a['last_balance'] or 'missing'}</td></tr>")
        unc = self.rows("""SELECT tx_id, date, account, payee, memo, amount FROM tx
                           WHERE category = 'Uncategorized' ORDER BY date DESC LIMIT 25""")
        n_unc = self.one("SELECT count(*) FROM tx WHERE category = 'Uncategorized'")
        unc_rows = "".join(
            f"<tr><td><code>{u['tx_id']}</code></td><td>{u['date']}</td><td>{html.escape(u['account'])}</td>"
            f"<td>{html.escape(u['payee'] or '')}</td><td class=memo>{html.escape(u['memo'] or '')}</td>"
            f"<td class=num>{eur(u['amount'], 2)}</td></tr>" for u in unc)
        fx_missing = self.rows("""SELECT currency, count(*) n FROM tx WHERE amount IS NULL GROUP BY 1
                                  UNION ALL SELECT currency, count(*) FROM balances_monthly WHERE balance_eur IS NULL GROUP BY 1""")
        fx_html = "".join(f'<p class="note warn-text">⚠ {r["n"]} amounts in {r["currency"]} have no rate in <code>fx.csv</code> and are left out.</p>'
                          for r in fx_missing)
        unc_html = fx_html + (f"""<p class="note">{n_unc} uncategorized transactions. Add a line to <code>rules.csv</code>
            (pattern → category) or a one-off fix to <code>overrides.csv</code> (tx_id → category), then rebuild.</p>
            <div class="scroll"><table><thead><tr><th>tx_id</th><th>Date</th><th>Account</th><th>Payee</th><th>Memo</th>
            <th class=num>Amount</th></tr></thead><tbody>{unc_rows}</tbody></table></div>"""
                    if n_unc else '<p class="note ok">✓ Every transaction is categorized.</p>')
        return f"""<div class="scroll"><table><thead><tr><th>Account</th><th>Last transaction</th><th class=num>Transactions</th>
            <th>Last balance</th></tr></thead><tbody>{''.join(lines)}</tbody></table></div>{unc_html}"""

    def net_worth_mix(self) -> str:
        """What net worth consists of in the latest month: one bar per bucket, colors as in the area chart."""
        r = self.rows("SELECT * FROM net_worth WHERE month = ?", self.latest)
        if not r:
            return ""
        r = r[0]
        parts = [("Cash", "cash", BLUE), ("Reserved", "reserved", YELLOW), ("Stocks & ETFs", "investments", AQUA),
                 ("Property", "property", VIOLET), ("Debt", "debt", ORANGE)]
        parts = sorted([(n, float(r[k]), slot) for n, k, slot in parts if r[k]], key=lambda x: x[1])
        # negative bars point left into the axis labels, so their value goes into the label instead
        parts = [((f"{n} ({eur(v / 1000)}k)" if v < 0 else n), v, slot) for n, v, slot in parts]
        fig = go.Figure()
        for name, value, slot in parts:
            fig.add_bar(y=[name], x=[value], orientation="h", name=name, meta={"slot": slot},
                        marker_color=LIGHT["series"][slot], text=[eur(value / 1000) + "k" if value > 0 else ""], textposition="outside",
                        cliponaxis=False, hovertemplate=f"{name}: %{{x:,.0f}} €<extra></extra>")
        base_layout(fig, height=60 + 44 * len(parts), barcornerradius=4, showlegend=False)
        fig.update_layout(bargap=0.35, margin=dict(l=8, r=48, t=8, b=8))
        fig.update_xaxes(showticklabels=False, showgrid=False,  # values sit at the bars
                         zeroline=True, zerolinecolor=LIGHT["axis"])
        fig.update_yaxes(showgrid=False, categoryorder="array", categoryarray=[n for n, _, _ in parts])
        return self.fig(fig)

    def net_worth(self) -> str:
        r = self.rows("SELECT * FROM net_worth ORDER BY month")
        x = [i["month"] for i in r]
        fig = go.Figure()
        series = [("Cash", "cash", BLUE)]
        if any(i["reserved"] for i in r):
            series.append(("Reserved", "reserved", YELLOW))
        series.append(("Investments", "investments", AQUA))
        if any(i["property"] for i in r):
            series.append(("Property", "property", VIOLET))
        for name, key, slot in series:
            fig.add_scatter(x=x, y=[float(i[key]) for i in r], name=name, stackgroup="assets", mode="lines",
                            line=dict(width=0, color=LIGHT["series"][slot]), meta={"slot": slot, "fill": True},
                            hovertemplate="%{y:,.0f} €")
        if any(i["debt"] for i in r):
            fig.add_scatter(x=x, y=[float(i["debt"]) for i in r], name="Debt", fill="tozeroy", mode="lines",
                            line=dict(width=0, color=LIGHT["series"][ORANGE]), meta={"slot": ORANGE, "fill": True},
                            hovertemplate="%{y:,.0f} €")
        fig.add_scatter(x=x, y=[float(i["net_worth"]) for i in r], name="Net worth", mode="lines+markers",
                        line=dict(width=2, color=LIGHT["ink"]), marker=dict(size=8), meta={"ink": "ink"},
                        hovertemplate="%{y:,.0f} €")
        base_layout(fig, hovermode="x unified")
        fig.update_yaxes(tickprefix="€", tickformat=",.0f")
        fig.update_xaxes(dtick="M1", tickformat="%b %y")
        return self.fig(fig)

    def loan(self) -> str:
        r = self.rows("""SELECT month, sum(remaining) remaining, sum(installment) installment,
                                sum(principal) principal, sum(interest) interest
                         FROM loan_monthly GROUP BY month ORDER BY month""")
        if not r:
            return ""
        x = [f'{i["month"]:%b %y}' for i in r]
        fig = go.Figure()
        fig.add_bar(x=x, y=[float(i["principal"]) for i in r], name="Principal repaid",
                    marker_color=LIGHT["series"][BLUE], meta={"slot": BLUE}, hovertemplate="%{y:,.0f} €")
        fig.add_bar(x=x, y=[float(i["interest"]) for i in r], name="Interest",
                    marker_color=LIGHT["series"][ORANGE], meta={"slot": ORANGE}, hovertemplate="%{y:,.0f} €")
        base_layout(fig, barmode="stack", hovermode="x unified", barcornerradius=4)
        fig.update_traces(marker_line_width=2, marker_line_color=LIGHT["paper"], selector=dict(type="bar"))
        fig.update_yaxes(tickprefix="€", tickformat=",.0f")
        fig.update_xaxes(type="category", tickangle=0, nticks=6)
        split = self.fig(fig)

        fig = go.Figure()
        fig.add_scatter(x=[i["month"] for i in r], y=[float(i["remaining"]) for i in r], name="Remaining debt",
                        mode="lines+markers", line=dict(width=2, color=LIGHT["series"][YELLOW]), marker=dict(size=8),
                        meta={"slot": YELLOW}, hovertemplate="%{y:,.0f} €")
        base_layout(fig, hovermode="x unified")
        fig.update_yaxes(tickprefix="€", tickformat=",.0f")
        fig.update_xaxes(tickformat="%b %y", nticks=6)
        remaining = self.fig(fig)

        last12 = [i for i in r if i["month"] > (self.month - timedelta(days=365))]
        interest12 = sum(float(i["interest"]) for i in last12)
        principal12 = sum(float(i["principal"]) for i in last12)
        return f"""<div class="two">
  <section class="card"><h2>Loan: interest vs. principal</h2>
  <p class="note">Interest = installment − drop in remaining debt. Last 12 months: <b>{eur(interest12)}</b> interest,
  <b>{eur(principal12)}</b> principal. Interest counts as an expense, principal as saving.</p>{split}</section>
  <section class="card"><h2>Loan: remaining debt</h2><p class="note">From month-end balances.</p>{remaining}</section>
</div>"""

    def investments(self) -> str:
        r = self.rows("""SELECT month, sum(deposits) deposits, sum(added) added, sum(market) market
                         FROM investment_monthly WHERE change IS NOT NULL AND month BETWEEN ? AND ?
                         GROUP BY month ORDER BY month""", self.start12, self.month)
        acc = self.rows("""SELECT a.name, i.value, s.deposits, s.added, s.market
                           FROM (SELECT account, sum(deposits) deposits, sum(added) added, sum(market) market
                                 FROM investment_monthly WHERE change IS NOT NULL AND month BETWEEN ? AND ? GROUP BY 1) s
                           JOIN accounts a USING (account)
                           LEFT JOIN investment_monthly i ON i.account = s.account AND i.month = ?
                           ORDER BY i.value DESC NULLS LAST""", self.start12, self.month, self.month)
        unvested = self.one("SELECT sum(value_eur) FROM unvested_monthly WHERE month = ?", self.month)
        if not r and not acc and not unvested:
            return ""
        chart = '<p class="note">Needs at least two months of values per investment account.</p>'
        if r:
            x = [f'{i["month"]:%b %y}' for i in r]
            fig = go.Figure()
            for name, key, slot in (("Deposits", "deposits", BLUE), ("New shares (vests)", "added", VIOLET),
                                    ("Market", "market", AQUA)):
                ys = [float(i[key] or 0) for i in r]
                if any(ys):
                    fig.add_bar(x=x, y=ys, name=name, marker_color=LIGHT["series"][slot], meta={"slot": slot},
                                hovertemplate="%{y:,.0f} €")
            base_layout(fig, barmode="relative", hovermode="x unified", barcornerradius=4)
            fig.update_traces(marker_line_width=2, marker_line_color=LIGHT["paper"], selector=dict(type="bar"))
            fig.update_yaxes(tickprefix="€", tickformat=",.0f")
            fig.update_xaxes(type="category", tickangle=0, nticks=6)
            chart = self.fig(fig)
        table = self.table(acc, [("name", "Account", ""), ("value", f"Value {self.month:%b %y}", "num"),
                                 ("deposits", "Deposits", "num"), ("added", "New shares", "num"),
                                 ("market", "Market", "num")])
        note = (f' Unvested shares worth about <b>{eur(unvested)}</b> are not counted in net worth.'
                if unvested else "")
        return f"""<section class="card"><h2>Investments: deposits vs. market</h2>
  <p class="note">Per month: money transferred in (minus out), newly vested shares, and the rest = market
  movement. Last 12 months.{note}</p>{chart}{table}</section>"""

    def net_worth_accounts(self) -> str:
        r = self.rows("""SELECT name, bucket, currency, balance, balance_eur, as_of FROM balances_monthly
                         WHERE month = ? AND balance <> 0 ORDER BY bucket, balance_eur DESC""", self.latest)
        rows = [dict(i, value=f"{i['balance']:,.2f} {i['currency']}" if i["currency"] != "EUR" else "") for i in r]
        return self.table(rows, [("name", "Account", ""), ("bucket", "Type", ""), ("value", "Original", "num-text"),
                                 ("balance_eur", f"EUR, {self.latest:%b %Y}", "num"), ("as_of", "Balance from", "")])

    def table(self, rows: list[dict], cols: list[tuple[str, str, str]]) -> str:
        if not rows:
            return '<p class="note">Nothing here yet.</p>'
        head = "".join(f'<th class="{c}">{h}</th>' for _, h, c in cols)
        body = ""
        for r in rows:
            cells = []
            for key, _, cls in cols:
                v = r[key]
                if cls == "num":
                    v = eur(v, 2) if v is not None else ""
                elif cls == "num-text":
                    cls = "num"
                cells.append(f'<td class="{cls}">{html.escape(str(v if v is not None else ""))}</td>')
            body += "<tr>" + "".join(cells) + "</tr>"
        return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    # ------------------------------------------------------------ page
    def render(self) -> Path:
        where_chart, takeaway = self.where()
        unc = self.rows("""SELECT count(*) n, -sum(amount) FILTER (amount < 0) s FROM tx_enriched
                           WHERE category = 'Uncategorized' AND month BETWEEN ? AND ?""", self.start12, self.month)[0]
        spent = float(self.one("""SELECT -sum(amount) FROM tx_enriched WHERE kind = 'expense'
                                  AND month BETWEEN ? AND ?""", self.start12, self.month) or 0)
        warn = ""
        if unc["s"] and spent and float(unc["s"]) / spent > 0.05:
            warn = (f'<p class="warnbox">{float(unc["s"]) / spent:.0%} of spending is still uncategorized '
                    f'({unc["n"]} transactions), so the category view is incomplete. '
                    f'<a href="#more">List in “More”</a>.</p>')
        period = f"{self.start12:%b %Y} – {self.month:%b %Y}"

        sections = f"""
<header>
  <h1>Household finances</h1>
  <p class="sub">{period} · data through {self.max_date:%d %b %Y}</p>
</header>
{warn}
<section class="tiles">{self.kpis()}</section>

<div class="split" id="networth">
  <section class="card"><h2>What is our net worth?</h2>{self.net_worth()}</section>
  <section class="card"><h2>What it consists of, {self.latest:%b %Y}</h2>{self.net_worth_mix()}</section>
</div>

<section class="card" id="time"><h2>How do we spend over time, compared to what comes in?</h2>{self.over_time()}</section>

<section class="card" id="flows"><h2>How does the money flow?</h2>
  <div class="tabs" role="tablist">
    <button role="tab" aria-selected="true" data-tab="flow12">Last 12 months</button>
    <button role="tab" aria-selected="false" data-tab="flow1">{self.month:%B %Y}</button>
  </div>
  <div class="tabpanel" id="flow12">{self.flows(self.start12, self.month)}</div>
  <div class="tabpanel" id="flow1" hidden>{self.flows(self.month, self.month)}</div>
</section>

<section class="card" id="where"><h2>Where does the money go, and where could we save?</h2>
<p class="takeaway">{takeaway}</p>{where_chart}</section>

<section class="card" id="unusual"><h2>Anything unusual?</h2>{self.unusual()}</section>

{self.after_move()}

{self.notes()}

<details id="more" class="card"><summary>More: accounts, investments, loan, data quality</summary>
<h3>Accounts, {self.latest:%B %Y}</h3>{self.net_worth_accounts()}
{self.investments()}
{self.loan()}
<h3>Data quality</h3>{self.data_quality()}
</details>
"""
        out = self.data / "out" / "report.html"
        out.write_text(PAGE.replace("{{BODY}}", sections)
                       .replace("{{PLOTLY}}", get_plotlyjs())
                       .replace("{{THEMES}}", json.dumps({"light": LIGHT, "dark": DARK})), encoding="utf-8")
        print(f"Report written to {out.relative_to(self.data)} ({self.month:%B %Y})")
        return out


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Household finances</title>
<style>
:root { color-scheme: light; --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --line:#e1e0d9; --ring:rgba(11,11,11,.10); --warn-bg:#fdf1d8; --warn:#8a5a00; --ok:#006300; --accent:#2a78d6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark; --page:#0d0d0d; --surface:#1a1a19;
  --ink:#f0efec; --ink2:#c3c2b7; --muted:#898781; --line:#2c2c2a; --ring:rgba(255,255,255,.10); --warn-bg:#3a2c0c; --warn:#fab219; --ok:#0ca30c; --accent:#3987e5; } }
:root[data-theme="dark"] { color-scheme: dark; --page:#0d0d0d; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --muted:#898781;
  --line:#2c2c2a; --ring:rgba(255,255,255,.10); --warn-bg:#3a2c0c; --warn:#fab219; --ok:#0ca30c; --accent:#3987e5; }
* { box-sizing: border-box; }
body { margin:0; background:var(--page); color:var(--ink); font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1180px; margin:0 auto; padding:24px 16px 64px; }
header h1 { font-size:24px; margin:0 0 4px; } .sub { color:var(--ink2); margin:0 0 20px; }
h2 { font-size:15px; margin:0 0 4px; } .note { color:var(--ink2); margin:0 0 12px; font-size:13px; } .note.ok { color:var(--ok); } .warn-text { color:var(--warn); }
.card { background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:16px; margin-bottom:16px; min-width:0; }
.two { display:grid; grid-template-columns:repeat(auto-fit, minmax(min(100%, 460px), 1fr)); gap:16px; }
.two > .card { margin-bottom:0; } .two { margin-bottom:16px; }
.split { display:grid; grid-template-columns:minmax(0,2fr) minmax(0,1fr); gap:16px; margin-bottom:16px; }
.split > .card { margin-bottom:0; }
@media (max-width: 640px) { .split { grid-template-columns:1fr; } }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(min(100%, 220px), 1fr)); gap:16px; margin-bottom:16px; }
.tile { background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:16px; }
.tile-label { color:var(--ink2); font-size:13px; } .tile-value { font-size:30px; font-weight:600; font-variant-numeric:tabular-nums; margin:2px 0; }
.tile-sub { color:var(--muted); font-size:12px; }
.scroll { overflow-x:auto; } table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--ink2); font-weight:500; } .num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
td.memo { color:var(--ink2); max-width:340px; } td.warn { background:var(--warn-bg); color:var(--warn); }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
.tabs { display:flex; gap:4px; margin-bottom:8px; flex-wrap:wrap; }
.tabs button { font:inherit; font-size:13px; padding:4px 12px; border-radius:999px; border:1px solid var(--line); background:transparent; color:var(--ink2); cursor:pointer; }
.tabs button[aria-selected="true"] { background:var(--ink); color:var(--surface); border-color:var(--ink); }
.plot { width:100%; min-width:0; overflow:hidden; }
a.tile { color:inherit; text-decoration:none; display:block; } a.tile:hover { border-color:var(--ink2); }
.warnbox { background:var(--warn-bg); color:var(--warn); border-radius:12px; padding:10px 14px; margin:0 0 16px; font-size:13px; }
.warnbox a { color:inherit; }
.takeaway { margin:0 0 12px; color:var(--ink2); }
ul.notes { margin:0 0 8px; padding-left:18px; color:var(--ink2); } ul.notes li { margin:3px 0; } ul.notes b { color:var(--ink); }
.list { list-style:none; padding:0; margin:0 0 8px; } .list li { padding:6px 0; border-bottom:1px solid var(--line); }
.amt { display:inline-block; min-width:80px; font-variant-numeric:tabular-nums; font-weight:600; } .dim { color:var(--muted); font-size:13px; }
details > summary { cursor:pointer; color:var(--ink2); font-weight:500; } details[open] > summary { margin-bottom:12px; }
h3 { font-size:14px; margin:20px 0 8px; } details .card { border:0; padding:0; background:transparent; }

</style>
<script>{{PLOTLY}}</script>
</head><body><main>
{{BODY}}
</main>
<script>
const THEMES = {{THEMES}};
function isDark() {
  const t = document.documentElement.dataset.theme;
  return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
}
function rgba(hex, a) { const h = hex.replace('#',''); return `rgba(${parseInt(h.slice(0,2),16)},${parseInt(h.slice(2,4),16)},${parseInt(h.slice(4,6),16)},${a})`; }
function applyTheme() {
  const t = isDark() ? THEMES.dark : THEMES.light;
  const seq = t.seq.map((c, i) => [i / (t.seq.length - 1), c]);
  document.querySelectorAll('.js-plotly-plot').forEach(gd => {
    const lay = { paper_bgcolor: t.paper, plot_bgcolor: t.paper, 'font.color': t.ink2 };
    Object.keys(gd.layout).filter(k => /^[xy]axis\\d*$/.test(k)).forEach(k => {
      lay[k + '.gridcolor'] = t.grid; lay[k + '.linecolor'] = t.axis;
    });
    Plotly.relayout(gd, lay);
    gd.data.forEach((tr, i) => {
      const m = tr.meta || {}; const u = {};
      if (m.slot !== undefined) {
        const c = t.series[m.slot];
        if (tr.type === 'bar') { u['marker.color'] = c; u['marker.line.color'] = t.paper; }
        else { u['line.color'] = c; if (m.fill) u.fillcolor = rgba(c, 0.85); }
      }
      if (m.ink) {
        if (m.marker_line) u['marker.line.color'] = t[m.ink];
        else { u['line.color'] = t[m.ink]; u['marker.color'] = t[m.ink]; }
      }
      if (m.muted) { u['marker.color'] = t.muted; u['marker.line.color'] = t.paper; }
      if (m.seq) u.colorscale = [seq];
      if (m.node_slots) {
        const col = s => s < 0 ? t.muted : t.series[s];
        u['node.color'] = [m.node_slots.map(col)];
        u['link.color'] = [m.link_src.map(s => rgba(col(m.node_slots[s]), 0.3))];
      }
      if (Object.keys(u).length) Plotly.restyle(gd, u, [i]);
    });
  });
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);
new MutationObserver(applyTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
function fit() { document.querySelectorAll('.tabpanel:not([hidden]) .js-plotly-plot, .card > .plot .js-plotly-plot').forEach(gd => Plotly.Plots.resize(gd)); }
window.addEventListener('load', () => { applyTheme(); fit(); });
new ResizeObserver(() => fit()).observe(document.querySelector('main'));
document.querySelectorAll('details').forEach(d => d.addEventListener('toggle', () => d.querySelectorAll('.js-plotly-plot').forEach(gd => Plotly.Plots.resize(gd))));
document.querySelectorAll('.tabs').forEach(tabs => tabs.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  tabs.querySelectorAll('button').forEach(x => {
    const on = x === b; x.setAttribute('aria-selected', on);
    const p = document.getElementById(x.dataset.tab); p.hidden = !on;
    if (on) p.querySelectorAll('.js-plotly-plot').forEach(gd => Plotly.Plots.resize(gd));
  });
}));
</script>
</body></html>
"""


if __name__ == "__main__":
    Report(data_dir(sys.argv)).render()
