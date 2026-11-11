"""Export local source passages for relevance review, without running retrieval.

This avoids building gold labels from the retriever's own top results. The ZIP
contains the candidate questions and all saved passages in their explicit scope,
including full structured tables. It excludes credentials, models and raw caches.
"""
import argparse
import json
from pathlib import Path
import zipfile

from filing_corpus import DEFAULT_CORPUS_DIR, _json_bytes
from filing_passages import DEFAULT_PASSAGES_DIR
from filing_retrieval import SearchRequest
from retrieval_data import PassageCatalogue


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions",type=Path,default=Path(__file__).with_name("retrieval_questions.jsonl"))
    parser.add_argument("--output",type=Path,default=Path("LSOutput/RetrievalReview.zip"))
    parser.add_argument("--case-ids",nargs="*")
    parser.add_argument("--passages-dir",type=Path,default=DEFAULT_PASSAGES_DIR)
    parser.add_argument("--corpus-dir",type=Path,default=DEFAULT_CORPUS_DIR)
    args=parser.parse_args()
    catalogue=PassageCatalogue(args.passages_dir,args.corpus_dir)
    questions=[json.loads(line) for line in args.questions.read_text().splitlines() if line.strip()]
    if args.case_ids:
        wanted=set(args.case_ids)
        questions=[q for q in questions if q["id"] in wanted]
        if {q["id"] for q in questions}!=wanted:raise ValueError("Unknown requested case ID.")
    selected,scopes={},{}
    for case in questions:
        r=SearchRequest.model_validate(case["request"])
        for t in r.tickers:
            for y in r.years:
                if (t,y) not in catalogue.documents:raise ValueError(f"Missing {t} FY{y} in local passages.")
        matches=[p for p in catalogue.passages.values() if p["ticker"] in r.tickers and p["fiscal_year"] in r.years
                 and (not r.items or p["item"] in r.items) and (r.kind=="all" or p["kind"]==r.kind)]
        scopes[case["id"]]=[p["passage_id"] for p in matches]
        selected.update({p["passage_id"]:p for p in matches})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.output.with_suffix(".tmp")
    try:
        with zipfile.ZipFile(temporary,"w",zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("questions.jsonl","\n".join(json.dumps(c,ensure_ascii=False) for c in questions)+"\n")
            archive.writestr("review_manifest.json",_json_bytes({"schema_version":1,"coverage":catalogue.summary(),"case_scopes":scopes,
                "instructions":"Review all relevant passages in each explicit scope before assigning gold IDs. Record rationale, source SHA-256, reviewer and reviewed status. No retrieved rankings are provided. These are development retrieval cases, not held-out agent questions."}))
            by_doc={}
            for p in selected.values():by_doc.setdefault((p["ticker"],p["fiscal_year"]),[]).append(p)
            for (ticker,year),values in sorted(by_doc.items()):
                archive.writestr(f"passages/{ticker}_{year}.json",_json_bytes(values))
        catalogue.verify_unchanged()
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"status":"ok","questions":len(questions),"source_passages":len(selected),"labelled_queries":0,
                      "output":str(args.output),"note":"Export only; human/source review is still required."},indent=2))
    return 0


if __name__=="__main__":raise SystemExit(main())
