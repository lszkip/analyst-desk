import json
from pathlib import Path

# Find the folder containing this Python script.
PROJECT_DIR = Path(__file__).resolve().parent

# Locate the financial data we downloaded.
facts_path = PROJECT_DIR / "data" / "raw" / "MSFT_companyfacts.json"

# Read the JSON file and turn it into a Python dictionary.
facts = json.loads(facts_path.read_text(encoding="utf-8"))

# Select the financial concept called "NetIncomeLoss".
net_income = facts["facts"]["us-gaap"]["NetIncomeLoss"]

print("Company:", facts["entityName"])
print("Financial concept:", net_income["label"])

# Get the list of reported net income amounts in US dollars.
records = net_income["units"]["USD"]

# Show the last five entries so we can inspect their dates and sources.
for record in records[-5:]:
    print()
    print("Period start:", record.get("start"))
    print("Period end:", record["end"])
    print(f"Net income: USD {record['val']:,}")
    print("Report type:", record["form"])
    print("Filed on:", record["filed"])
    print("Filing identifier:", record["accn"])

# Find the annual-report entries for this exact twelve-month period.
annual_records = [
    record
    for record in records
    if record["form"] == "10-K"
    and record.get("start") == "2025-07-01"
    and record["end"] == "2026-06-30"
]

print("\n--- Selected full-year net income ---")

if not annual_records:
    print("No matching annual-report entry was found.")
else:
    # Show every match, keeping its filing information for verification.
    for record in annual_records:
        print(f"Net income: USD {record['val']:,}")
        print("Period:", record["start"], "to", record["end"])
        print("Filed on:", record["filed"])
        print("Filing identifier:", record["accn"])
