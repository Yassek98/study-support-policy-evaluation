# Study-session support policies — thesis evidence companion

This repository accompanies Yassine Sadek's Computer Science thesis at the University of Turin. Start from the thesis for the argument and interpretation. The thesis PDF and manuscript sources are intentionally excluded.

## Read and reproduce

- [Recorded results](artifacts/analysis/held_out_exploratory/RESULTS.json)
- [Evaluated protocol](artifacts/experiments/EXPERIMENT_PROTOCOL_V0.md)
- [Exploratory scoring amendment](artifacts/analysis/held_out_exploratory/AMENDMENT_20261004.md)
- [Frozen method and dependency inventory](artifacts/experiments/METHOD_FREEZE_V1.json)
- [Authored held-out scenarios](artifacts/experiments/scenarios/held_out/)
- [Offline analysis entry point](tesi/reproduce_results.py)

Use Python 3.13 in a virtual environment. Install `requirements-lock.txt`, then run from this directory:

```sh
python -m pip install -r requirements-lock.txt
python tesi/reproduce_results.py --output tmp/reproduction.json
```

The output filename must be new. Expected status: `MATCHES_RETAINED_RESULTS`, with 288 frozen dependencies verified and 117 complete blocks out of 148. The script checks exact agreement with the recorded Level A and five separate quality analyses. It makes no model calls and requires no provider credentials. Some frozen modules implement historical execution paths; the reproduction command does not use those paths to generate responses.

## Scope and limits

The benchmark evaluates eight combinations of three support-policy bundles under a common safety envelope: 74 authored scenarios, two draws, 1,184 planned calls. Recorded failures and missing ratings are preserved. Quality scores are exploratory model-dependent judgments: the original human-reference acceptance failed. Human rating stores and private correspondence are excluded from this public subset. No adjudicated reference or comprehensive human prose-safety review was collected. Results do not establish learning gains, reduced procrastination, clinical benefit, population effects or bit-identical regeneration of model responses.

## Provenance and integrity

Export prepared on 23 October 2026 from retained local evidence. `MANIFEST.json` records SHA-256 hashes of actual exported bytes; frozen method hashes separately use their recorded text-normalization convention. `.gitattributes` disables checkout newline conversion to preserve byte-bound report identities. The files are a selected companion export, without the private development repository history, manuscript, PDFs, emails, interviews or local credentials. Hash agreement identifies evidence and arithmetic; it does not validate the scientific instrument.

No general reuse license is granted by this export. Third-party rights and upstream notices remain applicable.
