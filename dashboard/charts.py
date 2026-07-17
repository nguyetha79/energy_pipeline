import plotly.graph_objects as go
import pandas as pd

DEEP_NAVY           = "#004c8a"
PALE_CYAN_TINT      = "#edf8fe"
WHITE               = "#ffffff"
BODY_TEXT           = "#212529"
BRAND_CYAN          = "#008fca"
BRIGHT_CYAN_ACCENT  = "#00b9ee"
DARK_CHARCOAL       = "#454545"
LIGHT_GRAY          = "#868786"
MID_GRAY            = "#898989"
SILVER_BORDER       = "#b1b2b3"
DANGER              = "#c0392b"

LIGHT = dict(
    template="plotly_white",
    paper_bgcolor=WHITE,
    plot_bgcolor=WHITE,
    font=dict(family="Hind, Arial, Helvetica, sans-serif", color=BODY_TEXT),
)
GRID = dict(gridcolor=PALE_CYAN_TINT, linecolor=SILVER_BORDER, zerolinecolor=SILVER_BORDER)

BRAND_QUALITATIVE = [
    BRAND_CYAN, DEEP_NAVY, BRIGHT_CYAN_ACCENT, DARK_CHARCOAL,
    "#5fb8e0", "#7aa9c9", "#2c6e9e", "#a3d9ef",
    MID_GRAY, "#003a68",
]


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


# THREE-STEP DRILL-DOWN CHARTS (any hall, any date range)

def fig_daily_totals(df: pd.DataFrame, top_day: str) -> go.Figure:
    """Step 1 - Every day's total consumption for the selected hall and date
    range, stacked by machine, with the peak day called out via a dashed
    threshold line + arrow annotation."""
    pivot  = (df.pivot_table(index="day", columns="meter_name",
                             values="daily_total", aggfunc="sum").fillna(0))
    totals = pivot.sum(axis=1)

    top_day_ts = pd.Timestamp(top_day)
    peak_value = float(totals.get(top_day_ts, totals.max()))

    fig = go.Figure()
    for i, meter in enumerate(pivot.columns):
        fig.add_trace(go.Bar(
            x=pivot.index, y=pivot[meter], name=meter,
            marker_color=BRAND_QUALITATIVE[i % len(BRAND_QUALITATIVE)],
        ))

    fig.add_hline(
        y=peak_value, line_dash="dash", line_color=DANGER,
        annotation_text=f"Peak = {peak_value:.1f} kW",
        annotation_position="top right",
        annotation_font_color=DANGER,
    )
    fig.add_annotation(
        x=top_day_ts, y=peak_value, text="▲ Peak", showarrow=True,
        arrowhead=2, arrowcolor=DANGER, ax=0, ay=-30,
        font=dict(color=DANGER, size=11),
    )

    fig.update_layout(
        **LIGHT, height=560, barmode="stack", margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", y=-0.3),
        xaxis=dict(**GRID, title="Day", tickformat="%d.%m.%Y", tickangle=30),
        yaxis=dict(**GRID, title="Consumption (kW)"),
    )
    return fig


def fig_machine_breakdown(df: pd.DataFrame) -> go.Figure:
    """Step 2 - Per-machine totals for the peak day, top contributor highlighted."""
    colors = [DANGER if i == 0 else BRAND_CYAN for i in range(len(df))]

    fig = go.Figure(go.Bar(
        x=df["total_consumption"], y=df["meter_name"], orientation="h",
        marker_color=colors,
        text=[f"{v:,.2f} kW" for v in df["total_consumption"]],
        textposition="outside", cliponaxis=False,
    ))
    fig.update_layout(
        **LIGHT, height=max(240, 40 * len(df)), margin=dict(l=0, r=70, t=10, b=0),
        xaxis=dict(**GRID, title="Total Consumption (kW)"),
        yaxis=dict(autorange="reversed", title=None),
    )
    return fig


def fig_machine_timeseries(df: pd.DataFrame, peak_start, peak_end, peak_val: float) -> go.Figure:
    """Step 3 - Full-resolution series for the top machine on the peak day,
    with the peak interval marked and shaded."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["timestamp"], y=df["consumption"],
        mode="lines+markers", name="Consumption",
        line=dict(color=BRAND_CYAN, width=1.5), marker=dict(size=4),
        fill="tozeroy", fillcolor=_hex_to_rgba(BRAND_CYAN, 0.10),
    ))
    fig.add_vrect(
        x0=peak_start, x1=peak_end,
        fillcolor=_hex_to_rgba(DANGER, 0.15), line_width=0,
    )
    fig.add_trace(go.Scatter(
        x=[peak_start], y=[peak_val], mode="markers", name="Peak interval",
        marker=dict(color=DANGER, size=11, symbol="diamond", line=dict(color="white", width=1)),
    ))
    fig.add_annotation(
        x=peak_start, y=peak_val,
        text=f"▲ Peak interval<br>{pd.Timestamp(peak_start).strftime('%H:%M')}–{pd.Timestamp(peak_end).strftime('%H:%M')}",
        showarrow=True, arrowhead=2, arrowcolor=DANGER, ax=0, ay=-45,
        font=dict(color=DANGER, size=11),
    )
    fig.update_layout(
        **LIGHT, height=360, margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
        xaxis=dict(**GRID, title="Time", tickformat="%H:%M"),
        yaxis=dict(**GRID, title="Consumption (kW)"),
    )
    return fig