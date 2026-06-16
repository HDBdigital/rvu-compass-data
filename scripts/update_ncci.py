import requests
from bs4 import BeautifulSoup
import zipfile
import io
import pandas as pd
import json
import re
from datetime import date

CMS_PAGE_URL = "https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits/medicare-ncci-procedure-procedure-ptp-edits"

def find_latest_practitioner_zip_urls():
    """Scrape the CMS NCCI PTP page and find all ZIP links for the
    latest Practitioner PTP Edits version."""
    response = requests.get(CMS_PAGE_URL, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    links = soup.find_all("a", href=True)

    practitioner_links = []
    for link in links:
        text = link.get_text(strip=True)
        href = link["href"]
        if "Practitioner PTP Edits" in text and href.lower().endswith(".zip"):
            practitioner_links.append(href)

    if not practitioner_links:
        raise RuntimeError("No Practitioner PTP zip links found on CMS page")

    return practitioner_links


def download_and_extract_zip(url):
    """Download a zip file and return list of (filename, bytes) for
    contained files."""
    if url.startswith("/"):
        url = "https://www.cms.gov" + url

    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()

    zf = zipfile.ZipFile(io.BytesIO(response.content))

    extracted = []
    for name in zf.namelist():
        extracted.append((name, zf.read(name)))

    return extracted


def parse_ptp_file(file_bytes, filename):
    """Parse a single PTP edits file (xlsx or txt) into a DataFrame
    with columns: column1_code, column2_code, modifier_indicator."""

    if filename.lower().endswith(".xlsx") or filename.lower().endswith(".xls"):
        df = pd.read_excel(io.BytesIO(file_bytes), header=None)
    else:
        # tab or fixed width text fallback
        text = file_bytes.decode("utf-8", errors="ignore")
        rows = [line.split("\t") for line in text.splitlines() if line.strip()]
        df = pd.DataFrame(rows)

    # CMS PTP files typically have header rows before data starts.
    # Find the row where column 0 looks like a 5-character CPT/HCPCS code.
    code_pattern = re.compile(r"^[A-Z0-9]{5}$")

    data_start_idx = None
    for idx, row in df.iterrows():
        first_cell = str(row[0]).strip()
        if code_pattern.match(first_cell):
            data_start_idx = idx
            break

    if data_start_idx is None:
        return pd.DataFrame(columns=["column1_code", "column2_code", "modifier_indicator"])

    data = df.iloc[data_start_idx:].reset_index(drop=True)

    # Standard CMS PTP layout: col0=Column1 code, col1=Column2 code,
    # col2=Effective date, col3=Deletion date, col4=Modifier indicator
    cleaned = pd.DataFrame({
        "column1_code": data[0].astype(str).str.strip(),
        "column2_code": data[1].astype(str).str.strip(),
        "modifier_indicator": data[4].astype(str).str.strip() if data.shape[1] > 4 else "9"
    })

    cleaned = cleaned[cleaned["column1_code"].str.match(code_pattern)]
    cleaned = cleaned[cleaned["column2_code"].str.match(code_pattern)]

    return cleaned


def main():
    print("Finding latest Practitioner PTP zip files...")
    zip_urls = find_latest_practitioner_zip_urls()
    print(f"Found {len(zip_urls)} zip file(s)")

    all_dataframes = []

    for zip_url in zip_urls:
        print(f"Downloading: {zip_url}")
        files = download_and_extract_zip(zip_url)

        for filename, file_bytes in files:
            if filename.lower().endswith((".xlsx", ".xls", ".txt")):
                print(f"  Parsing {filename}")
                df = parse_ptp_file(file_bytes, filename)
                if not df.empty:
                    all_dataframes.append(df)

    if not all_dataframes:
        raise RuntimeError("No data parsed from any PTP file")

    combined = pd.concat(all_dataframes, ignore_index=True)
    combined = combined.drop_duplicates()

    # Only keep edits where indicator is 0 (never billable together)
    # or 1 (billable only with appropriate modifier) -- these are the
    # ones relevant for warning the user.
    combined = combined[combined["modifier_indicator"].isin(["0", "1"])]

    combined.to_csv("ncci_edits.csv", index=False)
    print(f"Wrote {len(combined)} edit pairs to ncci_edits.csv")

    manifest = {
        "version": date.today().strftime("%Y%m%d"),
        "release_date": date.today().isoformat(),
        "csv_url": "https://raw.githubusercontent.com/HDBdigital/rvu-compass-data/main/ncci_edits.csv",
        "description": "CMS NCCI Practitioner PTP Edits - auto-updated"
    }

    with open("ncci_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print("Wrote ncci_manifest.json")


if __name__ == "__main__":
    main()
