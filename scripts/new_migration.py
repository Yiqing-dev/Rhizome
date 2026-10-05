#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write a new Alembic revision for a model change (developers only).

    python scripts/new_migration.py "add note to entity"

Builds a scratch database at the current head, autogenerates the difference to the models (the
FTS tables are filtered out, see rhizome.db.session.include_object) into
backend/rhizome/migrations/versions/, and prints the file. Always read and edit the result: data
migrations, FTS changes and SQLite batch details are not generated."""

import sys
import tempfile
from pathlib import Path

from alembic import command

from rhizome.config import Settings, set_settings
from rhizome.db.session import _alembic_config, dispose_all, get_engine, head_revision, init_db

if len(sys.argv) != 2:
    sys.exit(__doc__)
with tempfile.TemporaryDirectory() as d:
    st = Settings(data_dir=Path(d), offline=True)
    set_settings(st)
    init_db(st)
    engine = get_engine(st)
    n = int(head_revision(engine) or "0") + 1
    with engine.begin() as conn:
        cfg = _alembic_config(engine)
        cfg.attributes["connection"] = conn
        script = command.revision(cfg, message=sys.argv[1], autogenerate=True, rev_id=f"{n:04d}")
    dispose_all()
print(script.path)
