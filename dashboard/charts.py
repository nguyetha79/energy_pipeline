import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

# ARNOLD Corporate Blue 
DEEP_NAVY          = "#004c8a"
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
        annotation_text=f"P95 = {threshold:.1f} kWh",
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
        colorbar=dict(title="kWh", outlinecolor=SILVER_BORDER),
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
        yaxis=dict(**GRID, title="Consumption (kWh)"),
        yaxis2=dict(overlaying="y", side="right", showgrid=False,
                    title="Peak (kWh)", tickfont=dict(color=DANGER)),
    )
    return fig


def fig_hall_drilldown(df: pd.DataFrame) -> go.Figure:
    pivot = (df.pivot_table(index="hour_bucket", columns="station_name",
                            values="total_consumption", aggfunc="sum").fillna(0))
    fig = go.Figure()
    for i, station in enumerate(pivot.columns):
        fig.add_trace(go.Scatter(
            x=pivot.index, y=pivot[station],
            name=station, mode="lines+markers",
            line=dict(color=BRAND_QUALITATIVE[i % len(BRAND_QUALITATIVE)], width=2),
            marker=dict(size=5),
        ))
    fig.update_layout(
        **LIGHT, height=320, margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
        xaxis=dict(**GRID, tickangle=30),
        yaxis=dict(**GRID, title="Consumption (kWh)"),
    )
    return fig

def fig_station_drilldown(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()

    for i, (station, sub) in enumerate(df.groupby("station_name")):
        sub = sub.sort_values("timestamp")
        color = BRAND_QUALITATIVE[i % len(BRAND_QUALITATIVE)]

        fig.add_trace(go.Scatter(
            x=sub["timestamp"], y=sub["consumption"],
            name=station,
            mode="lines",
            line=dict(color=color, width=1.5),
            # fill="tozeroy",
            # fillcolor=_hex_to_rgba(color, 0.12),
        ))

        # mark this station's peak point
        peak_idx = sub["consumption"].idxmax()
        peak_row = sub.loc[peak_idx]

        fig.add_trace(go.Scatter(
            x=[peak_row["timestamp"]], y=[peak_row["consumption"]],
            mode="markers+text",
            name=f"{station} peak",
            marker=dict(color=DANGER, size=10, symbol="diamond",
                       line=dict(color="white", width=1)),
            text=[f"{peak_row['consumption']:.1f}"],
            textposition="top center",
            textfont=dict(color=DANGER, size=10),
            showlegend=False,
        ))

    fig.update_layout(
        **LIGHT, height=360, margin=dict(l=0, r=0, t=10, b=0),
        showlegend=True,
        legend=dict(orientation="h", y=-0.2),
        xaxis=dict(**GRID, title="Time"),
        yaxis=dict(**GRID, title="Consumption (kWh)"),
    )
    return fig

def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"