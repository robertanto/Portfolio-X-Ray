import streamlit as st
import pandas as pd
import plotly.express as px
import yaml
from pathlib import Path
from portfolio_manager import PortfolioManager

# --- Page Configuration ---
st.set_page_config(
    page_title="Portfolio Insights",
    page_icon="📊",
    layout="wide"
)

# --- Custom CSS for "Lovely" look ---
st.markdown("""
    <style>
    .big-font {
        font-size:20px !important;
        color: #555;
    }
    .stMetric {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 10px;
    }
    .stExpander {
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        background-color: #ffffff;
    }
    </style>
    """, unsafe_allow_html=True)

# --- Session State Initialization ---
if 'portfolio_config' not in st.session_state:
    config_file = Path("portfolio.yaml")
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            st.session_state.portfolio_config = yaml.safe_load(f)
    else:
        # Default dummy data if file is missing
        st.session_state.portfolio_config = [
            {"url": "https://www.ishares.com/it/investitore-privato/it/prodotti/251850/", "weight": 1.0}
        ]

# --- Helper Functions ---

def config_to_df(config_list):
    """Converts the list-of-dicts config into a DataFrame for the editor."""
    rows = []
    for item in config_list:
        row = {
            "weight": item.get("weight", 0.0),
            "url": item.get("url", ""),
            "name": item.get("name", ""),
            "asset_class": item.get("asset_class", "")
        }
        rows.append(row)
    return pd.DataFrame(rows)

def df_to_config(df):
    """Converts the edited DataFrame back into the list-of-dicts format required by the Manager."""
    config = []
    for _, row in df.iterrows():
        item = {"weight": float(row["weight"])}
        
        # Logic: If URL is present, treat as ETF. Otherwise, treat as Manual Asset.
        url_val = str(row["url"]).strip()
        if url_val and url_val.lower() != "nan" and url_val.lower() != "none":
            item["url"] = url_val
        else:
            item["name"] = str(row["name"]).strip()
            item["asset_class"] = str(row["asset_class"]).strip()
        
        config.append(item)
    return config

@st.cache_data(show_spinner=False)
def run_analysis(config, skip_download):
    """
    Runs the portfolio analysis based on the provided config object.
    Cached based on the config content, so editing triggers a re-run.
    """
    manager = PortfolioManager(config)
    with st.spinner("Crunching numbers..."):
        try:
            manager.fetch_and_process(skip_download=skip_download)
            return manager.get_aggregated_views(), None
        except Exception as e:
            return None, str(e)

# --- Sidebar ---
st.sidebar.title("⚙️ Settings")
st.sidebar.markdown("Control data source.")

skip_dl = st.sidebar.checkbox(
    "Skip Download (Use Cache)", 
    value=True, 
    help="If checked, uses local CSV files in data/raw. Uncheck to force fresh download from iShares."
)

if st.sidebar.button("Clear Cache & Reload", type="primary"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.info("Edit the portfolio composition in the main area ➔")

# --- Main Content ---
st.title("📊 Portfolio X-Ray")

# --- 1. Editable Portfolio Section ---
with st.expander("📝 **Edit Portfolio Composition** (Live Sandbox)", expanded=False):
    st.caption("Modify weights, add ETFs (paste URL), or add manual assets (crypto/cash). Changes here do **not** overwrite your yaml file.")
    
    # Convert current session config to DF
    df_config = config_to_df(st.session_state.portfolio_config)
    
    # Configure columns for the editor
    column_config = {
        "weight": st.column_config.NumberColumn("Weight (0-1)", min_value=0.0, max_value=10.0, step=0.01, format="%.2f"),
        "url": st.column_config.TextColumn("ETF URL (iShares)", width="large", help="Leave empty for manual assets"),
        "name": st.column_config.TextColumn("Name (Manual)", help="Required only if URL is empty"),
        "asset_class": st.column_config.SelectboxColumn("Asset Class (Manual)", options=["Azionario", "Obbligazionario", "Crypto", "Commodity", "Liquidità", "Other"])
    }
    
    edited_df = st.data_editor(
        df_config, 
        column_config=column_config,
        num_rows="dynamic", 
        use_container_width=True
    )

    # Recalculate Logic
    current_total_weight = edited_df["weight"].sum()
    c1, c2 = st.columns([3, 1])
    c1.metric("Total Weight", f"{current_total_weight:.2f}", delta=f"{1.0 - current_total_weight:.2f} left" if current_total_weight != 1.0 else "Perfect", delta_color="off")
    
    if c2.button("🚀 Update Analysis", type="primary", use_container_width=True):
        # Convert back to config format and update session state
        new_config = df_to_config(edited_df)
        st.session_state.portfolio_config = new_config
        st.rerun()

# --- 2. Run Analysis ---
# We use the config from session state (which might have just been updated)
views, error = run_analysis(st.session_state.portfolio_config, skip_download=skip_dl)

if error:
    st.error(f"Error processing portfolio: {error}")
elif not views:
    st.warning("No data generated. Please check your inputs.")
else:
    # --- Safe Data Retrieval ---
    all_holdings = views.get("all_holdings", pd.DataFrame())
    global_asset = views.get("global_by_asset", pd.DataFrame())
    global_country = views.get("global_by_country", pd.DataFrame())
    global_sector = views.get("global_by_sector", pd.DataFrame())

    # --- Metrics Calculation ---
    col1, col2, col3, col4 = st.columns(4)
    
    if not global_asset.empty and "asset_class" in global_asset.columns:
        equity_exposure = global_asset[global_asset["asset_class"].str.lower() == "azionario"]["weight"].sum()
        bond_exposure = global_asset[global_asset["asset_class"].str.lower().str.contains("obbligazionario|bond")]["weight"].sum()
    else:
        equity_exposure = 0
        bond_exposure = 0

    if not global_country.empty:
        top_country_name = global_country.iloc[0]['country']
        top_country_pct = global_country.iloc[0]['weight']
    else:
        top_country_name = "N/A"
        top_country_pct = 0
        
    if not global_sector.empty:
        top_sector_name = global_sector.iloc[0]['sector']
        top_sector_pct = global_sector.iloc[0]['weight']
    else:
        top_sector_name = "N/A"
        top_sector_pct = 0

    col1.metric("Equity Allocation", f"{equity_exposure:.2%}")
    col2.metric("Bond Allocation", f"{bond_exposure:.2%}")
    col3.metric("Top Country", top_country_name, f"{top_country_pct:.2%}")
    col4.metric("Top Sector (Global)", top_sector_name, f"{top_sector_pct:.2%}")

    st.divider()

    # --- Tabs ---
    tab1, tab2, tab3, tab4 = st.tabs(["🌍 Overview", "📈 Equity Deep Dive", "🛡️ Bonds", "📄 Raw Data"])

    # TAB 1: OVERVIEW
    with tab1:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Asset Allocation")
            if not global_asset.empty:
                fig_asset = px.pie(global_asset, values='weight', names='asset_class', hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
                st.plotly_chart(fig_asset, use_container_width=True)
            else:
                st.info("No asset data available.")

        with c2:
            st.subheader("Global Geographic Exposure")
            if not global_country.empty:
                fig_geo = px.bar(global_country.head(10), x='weight', y='country', orientation='h', 
                                text_auto='.2%', title="Top 10 Countries (All Assets)", color='weight', color_continuous_scale='Bluyl')
                fig_geo.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_geo, use_container_width=True)
            else:
                st.info("No country data available.")

    # TAB 2: EQUITY
    with tab2:
        st.caption("Note: Normalized to 100% of your Equity portion.")
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Sector Breakdown")
            df_sector = views.get("equity_by_sector", pd.DataFrame())
            if not df_sector.empty:
                fig_sec = px.treemap(df_sector, path=['sector'], values='weight_norm', color='weight_norm', color_continuous_scale='RdBu')
                st.plotly_chart(fig_sec, use_container_width=True)
            else:
                st.info("No equity sector data.")
        
        with c2:
            st.subheader("Country Exposure (Equity)")
            df_eq_country = views.get("equity_by_country", pd.DataFrame())
            if not df_eq_country.empty:
                fig_eq_geo = px.bar(df_eq_country.head(10), x='weight_norm', y='country', orientation='h',
                                    text_auto='.2%', color='weight_norm', color_continuous_scale='Viridis')
                fig_eq_geo.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_eq_geo, use_container_width=True)
            else:
                st.info("No equity country data.")

        st.subheader("Top 15 Individual Stocks")
        df_stock = views.get("equity_by_stock", pd.DataFrame())
        if not df_stock.empty:
            fig_stock = px.bar(df_stock.head(15), x='name', y='weight_norm', color='weight_norm', title="Top Stocks")
            st.plotly_chart(fig_stock, use_container_width=True)
        else:
            st.info("No equity stock data.")

    # TAB 3: BONDS
    with tab3:
        st.caption("Note: Normalized to 100% of your Bond portion.")
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Bond Types (Top 99%)")
            df_bonds = views.get("bond_by_type", pd.DataFrame())
            if not df_bonds.empty:
                # Filter top 99%
                df_bonds_filtered = df_bonds.copy()
                df_bonds_filtered['cum_weight'] = df_bonds_filtered['weight_norm'].cumsum()
                mask = df_bonds_filtered['cum_weight'].shift(fill_value=0) < 0.99
                df_bonds_filtered = df_bonds_filtered[mask]

                fig_bonds = px.bar(df_bonds_filtered, x='weight_norm', y='sector', orientation='h',
                                   text_auto='.2%', color='weight_norm', color_continuous_scale='Blues')
                fig_bonds.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_bonds, use_container_width=True)
            else:
                st.info("No Bond exposure detected.")

        with c2:
            st.subheader("Country Exposure (Bonds)")
            df_bond_country = views.get("bond_by_country", pd.DataFrame())
            if not df_bond_country.empty:
                fig_bd_geo = px.bar(df_bond_country.head(10), x='weight_norm', y='country', orientation='h',
                                    text_auto='.2%', color='weight_norm', color_continuous_scale='Teal')
                fig_bd_geo.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_bd_geo, use_container_width=True)
            else:
                st.info("No bond country data.")

    # TAB 4: RAW DATA
    with tab4:
        st.markdown("### Explore all holdings")
        if all_holdings is not None and not all_holdings.empty:
            st.dataframe(all_holdings, use_container_width=True, height=600, column_config={
                "weight": st.column_config.NumberColumn("Weight", format="%.4f %%")
            })
            st.caption(f"Total rows: {len(all_holdings)}")
        else:
            st.warning("Raw holdings dataframe is empty.")

# --- Footer ---
st.markdown("---")
st.caption("Generated by Portfolio X-Ray | Based on iShares Data")