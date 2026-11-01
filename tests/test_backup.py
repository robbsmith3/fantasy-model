import zipfile

from fantasy_model import backup, config, db


def test_backup_zips_data_and_config_verifies_and_keeps_the_newest(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    cfg = tmp_path / "leagues.toml"
    cfg.write_text("[me]\n")
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "guillotine.duckdb")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    con = db.connect(data / "guillotine.duckdb")
    con.execute("INSERT INTO snapshot_run VALUES ('s1','daily',2026,8,now(),now(),true,NULL)")
    con.close()
    (data / "calibration.json").write_text("{}")

    dest = tmp_path / "backups"
    dest.mkdir()
    for i in range(3):  # older backups to prune
        (dest / f"fantasy-model-2020-01-0{i + 1}_0000.zip").write_bytes(b"old")
    r = backup.run(dest, keep=2)

    names = zipfile.ZipFile(r["archive"]).namelist()
    assert {"data/guillotine.duckdb", "data/calibration.json", "config/leagues.toml"} <= set(names)
    assert r["verified_runs"] == 1
    assert len(list(dest.glob("fantasy-model-*.zip"))) == 2 and r["pruned"] == 2
