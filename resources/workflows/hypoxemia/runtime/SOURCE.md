# Hypoxemia computation component

The supplied research backup was audited by following the platform's raw-processing,
training and evaluation commands and the model catalogue's Python imports. Only the
five-channel path is delivered here. No patient records, checkpoints, research results,
virtual environments or historical registries are part of this component.

## Imported application code

| Original relative path | Delivered path | Migration changes |
| --- | --- | --- |
| `scripts/run_tslib_classification_trial.py` | same | Dataset identity comes from the platform manifest; records runtime provenance |
| `scripts/tslib_classification_common.py` | same | Catalogue restricted to five-channel models; strict per-window schema, length, sampling and finite-value checks; reject missing groups; preserve split settings; source revision comes from bundled manifest |
| `scripts/tslib_local_models.py` | same | Retained the classes and helpers needed by the 19 local five-channel catalogue models; removed waveform/auxiliary model classes |
| `src/data_pipeline/monitor_data_pipeline/run_dedup_pipeline.py` | `raw/run_dedup_pipeline.py` | Require explicit input/output directories; declare 1 Hz and feature order in generated statistics |

`scripts/result_layout.py` is intentionally not imported. Its research dataset-name and
historical experiment layout rules are replaced by dataset manifest identity. Other
research scripts and unused vendor models are excluded. The vendor dependency closure
contains 47 Python files: 45 match the pinned upstream source and two are local additions.
The selected 44 catalogue models comprise 25 vendor models and 19 local compositions.

`evaluate_dataset.py` and `raw_zip_pipeline.py` were relocated from SOTABand's tool
implementation. The old paths remain thin command-line compatibility entries.
`scripts/provenance.py` is new SOTABand code. Platform code never imports torch as a
side effect of locating the component.

See `vendor/Time-Series-Library/NOTICE.md`, `LICENSE`, and `SOURCE.json` for upstream
provenance and exact file checksums. Input contract and installation instructions are
in `docs/hypoxemia`. Runtime files belong in the configured platform runtime root.
