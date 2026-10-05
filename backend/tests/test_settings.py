# SPDX-License-Identifier: Apache-2.0
"""Settings precedence and persistence: explicit > env > settings.json > defaults; only changes are
written; a broken file never stops the app."""

import json

from rhizome import config
from rhizome.config import Settings, load_settings, update_settings


def test_explicit_arguments_beat_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RHIZOME_DATA_DIR", str(tmp_path / "real-library"))
    monkeypatch.setenv("RHIZOME_DATABASE_URL", "sqlite:///elsewhere.db")
    st = Settings(data_dir=tmp_path / "snap", database_url="sqlite:///snap.db")
    assert st.data_dir == tmp_path / "snap" and st.database_url == "sqlite:///snap.db"


def test_environment_beats_file(tmp_path, monkeypatch):
    d = tmp_path / "lib"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"language": "en", "thresholds": {"merge_auto": 0.95}}), "utf-8")
    monkeypatch.setenv("RHIZOME_LANGUAGE", "zh_CN")
    st = load_settings(d)
    assert st.language == "zh_CN" and st.thresholds.merge_auto == 0.95  # nested file values still apply


def test_only_changed_keys_are_saved(tmp_path, monkeypatch):
    d = tmp_path / "lib"
    monkeypatch.setenv("RHIZOME_OFFLINE", "1")  # an env value must not be baked into the file
    config.set_settings(load_settings(d))
    config.set_overrides(port=54321)  # neither must a per-process override
    update_settings({"language": "zh_CN"})
    raw = json.loads((d / "settings.json").read_text("utf-8"))
    assert raw == {"language": "zh_CN", "settings_version": config.SETTINGS_VERSION}
    assert config.get_settings().port == 54321  # the override survives the reload
    update_settings({"thresholds": {"merge_auto": 0.95}})
    update_settings({"thresholds": {"merge_review": 0.5}})
    raw = json.loads((d / "settings.json").read_text("utf-8"))
    assert raw["thresholds"] == {"merge_auto": 0.95, "merge_review": 0.5}
    update_settings({"language": None, "thresholds": {"merge_auto": 0.9}})  # reset / back to default
    raw = json.loads((d / "settings.json").read_text("utf-8"))
    assert "language" not in raw and raw["thresholds"] == {"merge_review": 0.5}


def test_unknown_keys_from_a_newer_version_are_kept(tmp_path):
    d = tmp_path / "lib"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"future_option": 3}), "utf-8")
    update_settings({"language": "en"}, d)
    assert json.loads((d / "settings.json").read_text("utf-8"))["future_option"] == 3


def test_invalid_value_is_rejected_before_writing(tmp_path):
    d = tmp_path / "lib"
    update_settings({"language": "en"}, d)
    before = (d / "settings.json").read_text("utf-8")
    try:
        update_settings({"embedder": "nope"}, d)
        raise AssertionError("expected a validation error")
    except ValueError:
        pass
    assert (d / "settings.json").read_text("utf-8") == before


def test_broken_file_falls_back_to_backup_and_bad_fields_are_dropped(tmp_path):
    d = tmp_path / "lib"
    update_settings({"language": "zh_CN"}, d)
    update_settings({"review_daily_new": 7}, d)  # previous version now in settings.json.bak
    (d / "settings.json").write_text("{not json", "utf-8")
    st = load_settings(d)
    assert st.language == "zh_CN" and config.settings_problems(st)
    (d / "settings.json").write_text(json.dumps({"language": "zh_CN", "embedder": "gone-model"}), "utf-8")
    st = load_settings(d)
    assert st.language == "zh_CN" and st.embedder == "hashing"
    assert any("embedder" in p for p in config.settings_problems(st))
