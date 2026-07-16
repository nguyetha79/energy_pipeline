import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

# ARNOLD Corporate Blue 
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


def fig_overview(df: pd.DataFrame) -> go.Figure:
    threshold = df["total_consumption"].quantile(0.95)
    peaks_df  = df[df["total_consumption"] >= threshold]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["bucket"], y=df["total_consumption"],
        fill="tozeroy", name="Consumption",
        line=dict(color=BRAND_CYAN, width=1.5),
        fillcolor="rgba(0,143,202,0.10)",
    ))
    fig.add_trace(go.Scatter(
        x=peaks_df["bucket"], y=peaks_df["total_consumption"],
        mode="markers", name="Peak (≥P95)",
        marker=dict(color=DANGER, size=7),
    ))
    fig.add_hline(
        y=threshold, line_dash="dash", line_color=DEEP_NAVY,
        annotation_text=f"P95 = {threshold:.1f} kW",
        annotation_position="top right",
        annotation_font_color=DEEP_NAVY,
    )
    fig.update_layout(**LIGHT, height=320, margin=dict(l=0, r=0, t=10, b=0),
                      showlegend=False, xaxis=GRID, yaxis=GRID)
    return fig


def fig_by_hall(df: pd.DataFrame) -> go.Figure:
    pivot = (df.pivot_table(index="bucket", columns="hall_id",
                            values="total_consumption", aggfunc="sum").fillna(0))
    fig = go.Figure()
    for i, hall in enumerate(pivot.columns):
        fig.add_trace(go.Scatter(
            x=pivot.index, y=pivot[hall], name=hall,
            stackgroup="one", line=dict(width=0.5),
            fillcolor=BRAND_QUALITATIVE[i % len(BRAND_QUALITATIVE)],
        ))
    fig.update_layout(**LIGHT, height=320, margin=dict(l=0, r=0, t=10, b=0),
                      legend=dict(orientation="h", y=-0.2), xaxis=GRID, yaxis=GRID)
    return fig


def fig_heatmap(df: pd.DataFrame) -> go.Figure:
    pivot = (df.groupby(["hall_id", "hour_of_day"])["avg_consumption"]
               .mean().unstack())
    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[f"{int(h):02d}:00" for h in pivot.columns],
        y=pivot.index,
        colorscale=[
            [0.0, PALE_CYAN_TINT],
            [0.5, BRAND_CYAN],
            [1.0, DEEP_NAVY],
        ],
        colorbar=dict(title="kW", outlinecolor=SILVER_BORDER),
    ))
    fig.update_layout(**LIGHT, height=280, margin=dict(l=0, r=0, t=10, b=0),
                      xaxis_title="Hour of Day", yaxis_title="Hall")
    return fig


def fig_daily_peak(df: pd.DataFrame, top_n: int) -> go.Figure:
    pivot      = (df.pivot_table(index="day", columns="hall_id",
                                 values="daily_total", aggfunc="sum").fillna(0))
    daily_peak = df.groupby("day")["daily_peak"].max()
    top_days   = daily_peak.nlargest(top_n)

    fig = go.Figure()
    for i, hall in enumerate(pivot.columns):
        fig.add_trace(go.Bar(
            x=pivot.index, y=pivot[hall],
            name=hall, marker_color=BRAND_QUALITATIVE[i % len(BRAND_QUALITATIVE)],
        ))
    fig.add_trace(go.Scatter(
        x=daily_peak.index, y=daily_peak.values,
        mode="lines+markers", name="Daily Peak",
        line=dict(color=DANGER, width=2, dash="dot"),
        marker=dict(size=5), yaxis="y2",
    ))
    for day, val in top_days.items():
        fig.add_annotation(
            x=day, y=val, yref="y2", text=f"▲{val:.0f}",
            showarrow=False, yanchor="bottom",
            font=dict(color=DANGER, size=9),
        )
    fig.update_layout(
        **LIGHT, height=360, barmode="stack", margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", y=-0.2), xaxis=GRID,
        yaxis=dict(**GRID, title="Consumption (kW)"),
        yaxis2=dict(overlaying="y", side="right", showgrid=False,
                    title="Peak (kW)", tickfont=dict(color=DANGER)),
    )
    return fig


'''def fig_hall_drilldown(df: pd.DataFrame) -> go.Figure:
    pivot = (df.pivot_table(index="hour_bucket", columns="meter_name",
                            values="total_consumption", aggfunc="sum").fillna(0))
    fig = go.Figure()
    for i, meter in enumerate(pivot.columns):
        fig.add_trace(go.Scatter(
            x=pivot.index, y=pivot[meter],
            name=meter, mode="lines+markers",
            line=dict(color=BRAND_QUALITATIVE[i % len(BRAND_QUALITATIVE)], width=2),
            marker=dict(size=5),
        ))
    fig.update_layout(
        **LIGHT, height=320, margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
        xaxis=dict(**GRID, tickangle=30),
        yaxis=dict(**GRID, title="Consumption (kW)"),
    )
    return fig'''

def fig_hall_drilldown(df: pd.DataFrame) -> go.Figure:
    pivot = (df.pivot_table(index="bucket", columns="meter_name",
                            values="total_consumption", aggfunc="sum").fillna(0))
 
    # total per 15-min bucket, across all meters stacked - this is what
    # actually crossed the threshold, not any single meter on its own
    totals      = pivot.sum(axis=1)
    peak_bucket = totals.idxmax()
    peak_value  = totals.max()
 
    fig = go.Figure()
    for i, meter in enumerate(pivot.columns):
        fig.add_trace(go.Bar(
            x=pivot.index, y=pivot[meter], name=meter,
            marker_color=BRAND_QUALITATIVE[i % len(BRAND_QUALITATIVE)],
        ))
 
    # threshold line at the peak level, same visual language as fig_overview
    fig.add_hline(
        y=peak_value, line_dash="dash", line_color=DANGER,
        annotation_text=f"Peak = {peak_value:.1f} kW",
        annotation_position="top right",
        annotation_font_color=DANGER,
    )
 
    # call out which bucket the peak actually happened in
    fig.add_annotation(
        x=peak_bucket, y=peak_value, text="▲ Peak", showarrow=True,
        arrowhead=2, arrowcolor=DANGER, ax=0, ay=-30,
        font=dict(color=DANGER, size=11),
    )
 
    fig.update_layout(
        **LIGHT, height=660, barmode="stack", margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", y=-0.3),
        xaxis=dict(**GRID, title="Time", tickangle=30, tickformat="%d.%m.%Y %H:%M"),
        yaxis=dict(**GRID, title="Consumption (kW)"),
    )
    return fig

def fig_peak_contributors(df: pd.DataFrame) -> go.Figure:
    pivot  = (df.pivot_table(index="bucket", columns="meter_name",
                             values="total_consumption", aggfunc="sum").fillna(0))
    totals      = pivot.sum(axis=1)
    peak_bucket = totals.idxmax()
 
    buckets  = list(pivot.index)
    peak_pos = buckets.index(peak_bucket)
 
    if peak_pos == 0:
        # no earlier interval in this window to compare against
        delta          = pivot.loc[peak_bucket].sort_values(ascending=False)
        baseline_label = "start of window"
    else:
        prev_bucket    = buckets[peak_pos - 1]
        delta          = (pivot.loc[peak_bucket] - pivot.loc[prev_bucket]).sort_values(ascending=False)
        baseline_label = pd.Timestamp(prev_bucket).strftime("%d.%m.%Y %H:%M")
 
    peak_label = pd.Timestamp(peak_bucket).strftime("%d.%m.%Y %H:%M")
    colors     = [DANGER if v > 0 else MID_GRAY for v in delta.values]
 
    fig = go.Figure(go.Bar(
        x=delta.values, y=delta.index, orientation="h",
        marker_color=colors,
        text=[f"{v:+.2f} kW" for v in delta.values],
        textposition="outside",
        cliponaxis=False,
    ))
    fig.add_vline(x=0, line_color=SILVER_BORDER)
    fig.update_layout(
        **LIGHT, height=max(240, 32 * len(delta)),
        margin=dict(l=0, r=60, t=40, b=0),
        title=dict(
            text=f"Δ Consumption per meter: {baseline_label} → {peak_label} (peak)",
            font=dict(size=13, color=DEEP_NAVY),
        ),
        xaxis=dict(**GRID, title="Change in consumption (kW)"),
        yaxis=dict(autorange="reversed", title=None),
    )
    return fig

def fig_meter_drilldown(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()

    for i, (meter, sub) in enumerate(df.groupby("meter_name")):
        sub   = sub.sort_values("timestamp")
        color = BRAND_QUALITATIVE[i % len(BRAND_QUALITATIVE)]

        fig.add_trace(go.Scatter(
            x=sub["timestamp"], y=sub["consumption"],
            name=meter,
            mode="lines",
            line=dict(color=color, width=1.5),
        ))

        # Peak marker with rich tooltip
        peak_idx = sub["consumption"].idxmax()
        peak_row = sub.loc[peak_idx]

        meter_id = peak_row.get("meter_id", "—")  # graceful fallback

        fig.add_trace(go.Scatter(
            x=[peak_row["timestamp"]],
            y=[peak_row["consumption"]],
            mode="markers",
            name=f"{meter} peak",
            marker=dict(color=DANGER, size=10, symbol="diamond",
                        line=dict(color="white", width=1)),
            
            customdata=[[peak_row["consumption"], meter]],
            hovertemplate=(
                "<b>⚡ Peak</b><br>"
                "Value:   <b>%{customdata[0]:.1f} kW</b><br>"
                "Meter: <b>%{customdata[1]}</b><br>"
                "Time:    %{x|%d.%m.%Y %H:%M}"
                "<extra></extra>"   # hides the trace name box
            ),
            showlegend=False,
        ))

    fig.update_layout(
        **LIGHT, height=360, margin=dict(l=0, r=0, t=10, b=0),
        showlegend=True,
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(**GRID, title="Time"),
        yaxis=dict(**GRID, title="Consumption (kW)"),
        hoverlabel=dict(
            bgcolor=WHITE,
            bordercolor=SILVER_BORDER,
            font=dict(family="Hind, Arial, sans-serif", color=BODY_TEXT, size=12),
        ),
    )
    return fig

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"





# H71 THREE-STEP DRILL-DOWN CHARTS
 
'''def fig_h71_daily_totals(df: pd.DataFrame, top_day: str) -> go.Figure:
    """Step 1 - Every day's total consumption for H71, peak day highlighted."""
    top_day_ts = pd.Timestamp(top_day)
    colors = [DANGER if d == top_day_ts else BRAND_CYAN for d in df["day"]]
 
    fig = go.Figure(go.Bar(
        x=df["day"], y=df["daily_total"],
        marker_color=colors, name="Daily Total",
    ))
 
    peak_val = df.loc[df["day"] == top_day_ts, "daily_total"]
    if not peak_val.empty:
        fig.add_annotation(
            x=top_day_ts, y=peak_val.iloc[0],
            text=f"▲ Peak day<br>{top_day_ts.strftime('%d.%m.%Y')}",
            showarrow=True, arrowhead=2, arrowcolor=DANGER, ax=0, ay=-35,
            font=dict(color=DANGER, size=11),
        )
 
    fig.update_layout(
        **LIGHT, height=320, margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
        xaxis=dict(**GRID, title="Day"),
        yaxis=dict(**GRID, title="Total Consumption (kW)"),
    )
    return fig
 '''

def fig_h71_daily_totals(df: pd.DataFrame, top_day: str) -> go.Figure:
    """Step 1 - Every day's total consumption for H71, stacked by machine
    (same visual language as the hall drill-down chart), with the peak
    day called out via a dashed threshold line + arrow annotation."""
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

def fig_h71_machine_breakdown(df: pd.DataFrame) -> go.Figure:
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
 
 
def fig_h71_machine_timeseries(df: pd.DataFrame, peak_start, peak_end, peak_val: float) -> go.Figure:
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