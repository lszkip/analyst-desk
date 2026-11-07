import json  # Converts between JSON text and Python dictionaries.
import os  # Reads settings loaded from .env.
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from dotenv import load_dotenv

# Locate the project folder and load its settings.
PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

# Read our identification for requests to the SEC.
user_agent = os.getenv("SEC_USER_AGENT")

if not user_agent:
    raise SystemExit("Add SEC_USER_AGENT to .env and save it.")

# Microsoft has this company identifier in the SEC database.
CIK = "0000789019"
url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"

# Create a folder for downloaded source data.
data_dir = PROJECT_DIR / "data" / "raw"
data_dir.mkdir(parents=True, exist_ok=True)

facts_path = data_dir / "MSFT_companyfacts.json"
metadata_path = data_dir / "MSFT_companyfacts_metadata.json"

# Reuse the existing download when we run this script again.
if facts_path.exists():
    print("Using the previously downloaded Microsoft data.")
    facts = json.loads(facts_path.read_text(encoding="utf-8"))

else:
    print("Downloading Microsoft financial facts from the SEC...")

    # Attach our identification to the outgoing request.
    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
        },
    )

    # Download the response, allowing up to 30 seconds for network operations.
    with urlopen(request, timeout=30) as response:
        raw_data = response.read()

    # Parse and check the response before saving it.
    facts = json.loads(raw_data)

    if facts.get("cik") != int(CIK) or "facts" not in facts:
        raise SystemExit("Unexpected SEC response; no file was saved.")

    # Preserve the downloaded JSON.
    facts_path.write_bytes(raw_data)

    # Record where the data came from and when we downloaded it.
    metadata = {
        "ticker": "MSFT",
        "cik": CIK,
        "source_url": url,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }

    metadata_path.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

# Show a short summary instead of printing the whole dataset.
concepts = facts["facts"].get("us-gaap", {})

print("Company:", facts["entityName"])
print("Number of US-GAAP financial concepts:", len(concepts))
print("Data file:", facts_path)