import re
import urllib.parse
import requests
import pandas as pd
from bs4 import BeautifulSoup
import yaml
import logging
from pathlib import Path
from typing import List, Dict, Optional, Union

# Logging Configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants and Configurations
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; iSharesPortfolioBot/1.0)"}
SEARCH_TEXT_IT = "informazioni dettagliate sulle partecipazioni"

# Directory Configuration
DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

COLUMN_MAPPING = {
    "ticker dell'emittente": "ticker",
    "nome": "name",
    "settore": "sector",
    "area geografica": "country",
    "asset class": "asset_class",
    "valore di mercato": "market_value",
    "ponderazione (%)": "weight"
}

class ISharesDownloader:
    """Handles downloading CSVs from the iShares website."""
    
    @staticmethod
    def get_csv_url(etf_page_url: str) -> str:
        """Scrapes the ETF page to find the link to the holdings CSV."""
        try:
            r = requests.get(etf_page_url, headers=HEADERS)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            for a in soup.find_all("a", href=True):
                text = (a.get_text() or "").strip().lower()
                if SEARCH_TEXT_IT in text:
                    return urllib.parse.urljoin(etf_page_url, a["href"])
            
            raise RuntimeError(f"CSV link not found for: {etf_page_url}")
        except Exception as e:
            logger.error(f"Error retrieving CSV URL: {e}")
            raise

    @staticmethod
    def download_file(url: str, save_path: Path) -> Path:
        """Downloads the file from the specified URL."""
        if save_path.exists():
            logger.info(f"Existing file skipped (manage skip_download to force retry): {save_path}")
            return save_path

        logger.info(f"Downloading: {url} -> {save_path}")
        r = requests.get(url, headers=HEADERS)
        r.raise_for_status()
        
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(r.content)
        return save_path

class CSVProcessor:
    """Handles cleaning and parsing of raw CSV files."""

    @staticmethod
    def clean_csv(input_path: Path, output_dir: Optional[Path] = None) -> Path:
        """Removes spurious headers and disclaimers at the end of the file."""
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{input_path.stem}_clean.csv"
        else:
            output_path = input_path.with_name(f"{input_path.stem}_clean.csv")
        
        try:
            with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            header_pattern = re.compile(r"^\s*Ticker dell", re.IGNORECASE)
            start_index = next((i for i, line in enumerate(lines) if header_pattern.match(line)), None)

            if start_index is None:
                raise ValueError(f"Header not found in {input_path}")

            # Filter useful lines
            cleaned_lines = lines[start_index:]
            
            # Find the end (remove disclaimers)
            end_index = len(cleaned_lines)
            for i, line in enumerate(cleaned_lines):
                if line.strip().startswith(("Questo documento", "The content")):
                    end_index = i
                    break
            
            with open(output_path, "w", encoding="utf-8", newline="") as f:
                f.writelines(cleaned_lines[:end_index])
                
            return output_path
        except Exception as e:
            logger.error(f"Error cleaning CSV {input_path}: {e}")
            raise

    @staticmethod
    def parse_holdings(clean_csv_path: Path) -> pd.DataFrame:
        """Reads the cleaned CSV and normalizes columns."""
        df = pd.read_csv(clean_csv_path)
        df.columns = [c.strip().lower() for c in df.columns]
        
        # Rename columns
        df = df.rename(columns=COLUMN_MAPPING)
        
        # Ensure all target columns exist
        required_cols = ["ticker", "name", "sector", "country", "asset_class", "weight"]
        for col in required_cols:
            if col not in df.columns:
                df[col] = None

        # Numeric conversion (European format: 1.000,00 -> 1000.00)
        if "weight" in df.columns and df["weight"].dtype == object:
            df["weight"] = (
                df["weight"].astype(str)
                .str.replace(".", "", regex=False)
                .str.replace(",", ".", regex=False)
            )
            df["weight"] = pd.to_numeric(df["weight"], errors='coerce').fillna(0.0)

        # String normalization
        str_cols = ["ticker", "name", "sector", "country", "asset_class"]
        for col in str_cols:
            df[col] = df[col].astype(str).str.strip()

        # Remove empty rows
        df = df[df["name"] != "nan"].reset_index(drop=True)
        
        # Normalize to 1 (100%)
        total_weight = df["weight"].sum()
        if total_weight > 0:
            df["weight"] = df["weight"] / total_weight

        return df[required_cols]

class PortfolioManager:
    """Handles portfolio aggregation."""
    
    def __init__(self, portfolio_config: List[Dict]):
        self.config = portfolio_config
        self.holdings = []
        
    def fetch_and_process(self, skip_download: bool = False):
        """Executes the main flow: download, clean, parse."""
        downloader = ISharesDownloader()
        processor = CSVProcessor()
        
        # Ensure directories exist
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        
        data_frames = []

        for item in self.config:
            allocation_weight = item.get("weight", 0)
            
            if "url" in item:
                # Handle ETF
                url = item["url"]
                etf_name = re.sub(r"[^A-Za-z0-9]+", "_", urllib.parse.urlparse(url).path.strip("/"))
                
                # Paths
                csv_path = RAW_DIR / f"{etf_name}.csv"
                
                try:
                    if not skip_download:
                        real_csv_link = downloader.get_csv_url(url)
                        downloader.download_file(real_csv_link, csv_path)
                    elif not csv_path.exists():
                        # If skip download is requested but file doesn't exist, we must force download
                        logger.warning(f"File {csv_path} missing. Forcing download despite skip flag.")
                        real_csv_link = downloader.get_csv_url(url)
                        downloader.download_file(real_csv_link, csv_path)

                    
                    # Save cleaned version to processed directory
                    clean_path = processor.clean_csv(csv_path, output_dir=PROCESSED_DIR)
                    df = processor.parse_holdings(clean_path)
                    
                    # Scale weights based on portfolio allocation
                    df["weight"] = df["weight"] * allocation_weight
                    data_frames.append(df)
                    
                except Exception as e:
                    logger.error(f"Processing failure {url}: {e}")
            else:
                # Handle Manual Assets
                df = pd.DataFrame([{
                    "ticker": item.get("name", "UNKNOWN").upper(),
                    "name": item.get("name", "Unknown"),
                    "asset_class": item.get("asset_class", "Other"),
                    "sector": "Manual",
                    "country": "Manual",
                    "weight": allocation_weight
                }])
                data_frames.append(df)

        if data_frames:
            self.holdings = pd.concat(data_frames, ignore_index=True)
        else:
            self.holdings = pd.DataFrame()

    def get_aggregated_views(self) -> Dict[str, pd.DataFrame]:
        """Creates various aggregated views (by stock, sector, etc.)."""
        if self.holdings.empty:
            return {}
            
        df = self.holdings.copy()
        
        def group_sort(dataframe, group_cols):
            return dataframe.groupby(group_cols)["weight"].sum().reset_index().sort_values("weight", ascending=False)

        equities = df[df["asset_class"].str.lower() == "azionario"].copy()
        if not equities.empty:
            equities["weight_norm"] = equities["weight"] / equities["weight"].sum()
        else:
            equities["weight_norm"] = 0

        bonds = df[df["asset_class"].str.lower().str.contains("obbligazionario|bond")].copy()
        if not bonds.empty:
            bonds["weight_norm"] = bonds["weight"] / bonds["weight"].sum()
        else:
             bonds["weight_norm"] = 0

        return {
            "global_by_asset": group_sort(df, "asset_class"),
            "global_by_country": group_sort(df, "country"),
            "global_by_sector": group_sort(df, "sector"),
            
            # Equity Views
            "equity_by_stock": equities.groupby(["ticker", "name"])["weight_norm"].sum().reset_index().sort_values("weight_norm", ascending=False),
            "equity_by_sector": equities.groupby("sector")["weight_norm"].sum().reset_index().sort_values("weight_norm", ascending=False),
            "equity_by_country": equities.groupby("country")["weight_norm"].sum().reset_index().sort_values("weight_norm", ascending=False),

            # Bond Views
            "bond_by_type": bonds.groupby("sector")["weight_norm"].sum().reset_index().sort_values("weight_norm", ascending=False),
            "bond_by_country": bonds.groupby("country")["weight_norm"].sum().reset_index().sort_values("weight_norm", ascending=False),
            
            "all_holdings": df.sort_values("weight", ascending=False)
        }

class ReportGenerator:
    """Handles output to console or file."""
    
    @staticmethod
    def print_summary(views: Dict[str, pd.DataFrame]):
        if not views:
            print("No data to display.")
            return
        # (Logic retained for fallback use)
        print("Data loaded successfully.")

    @staticmethod
    def export_excel(views: Dict[str, pd.DataFrame], filename: str = "portfolio_analysis.xlsx"):
        with pd.ExcelWriter(filename) as writer:
            for sheet_name, df in views.items():
                safe_name = sheet_name[:31]
                df.to_excel(writer, sheet_name=safe_name, index=False)
        logger.info(f"Export completed: {filename}")

def main():
    # (Logic retained for standalone testing)
    pass

if __name__ == "__main__":
    main()