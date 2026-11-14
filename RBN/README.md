rbn-openspace/
├── pipeline.sh               # End-to-end orchestration script
├── generate_colortables.py   # Generates OpenSpace transfer function .txt files
├── station_resolver.py       # SQLite cache + Skimmer API + QRZ XML + cty.dat fallback
├── arc_builder.py            # Great-circle SLERP arc geometry + .osfls binary writer
├── fetch_rbn_history.py      # Historical daily archive fetcher (data.reversebeacon.net)
├── fetch_rbn_live.py         # Recent/live API fetcher (vailrerbn.com) for Phase 2
├── rbn_fieldlines.asset      # OpenSpace Lua asset with GUI-switchable ColorQuantity
├── data/                     # Auto-created: raw RBN zips, cty.dat, and station_cache.db
└── output/
    ├── colortables/          # Generated .txt transfer functions
    └── osfls/                # Generated YYYY-MM-DDThh-mm-ss-000.osfls sequence


Install requirements:
 pip install numpy requests

chmod +x pipeline.sh
QRZ_USER="your_callsign" QRZ_PASS="your_password" ./pipeline.sh