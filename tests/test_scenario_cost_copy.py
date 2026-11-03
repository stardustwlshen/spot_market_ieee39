"""New scenes inherit optional cost archives without changing existing scenes."""
import pytest

from smm import scenario


OPTIONAL = ("fixed_costs.yaml", "bid_cost_basis.yaml")


def seed_source(root, optional=OPTIONAL):
    (root / "config").mkdir(parents=True)
    for name in ("base.yaml", "units.yaml", "modes.yaml", *optional):
        (root / "config" / name).write_bytes(f"# 原始注释 {name}\nvalue: 17\n".encode())
    (root / "data" / "case39").mkdir(parents=True)
    (root / "data" / "case39" / "buses.csv").write_text("bus\n1\n")


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("optional", [(), OPTIONAL[:1], OPTIONAL[1:], OPTIONAL])
@pytest.mark.parametrize("from_scene", [False, True])
def test_new_scene_copies_present_optional_cost_files(tmp_path, monkeypatch, optional, from_scene):
    repo = tmp_path / "repo"
    scenes = tmp_path / "scenes"
    monkeypatch.setattr(scenario, "ROOT", repo)
    monkeypatch.setattr(scenario, "SCEN_DIR", scenes)
    source = scenes / "来源" if from_scene else repo
    seed_source(source, optional)
    before = snapshot(source)

    target = scenario.create_scenario("新场景", source="来源" if from_scene else None)

    for name in OPTIONAL:
        copied = target / "config" / name
        assert copied.exists() == (name in optional)
        if name in optional:
            assert copied.read_bytes() == (source / "config" / name).read_bytes()
    assert snapshot(source) == before
    assert (target / "scenario.json").is_file()


def test_create_existing_scene_never_overwrites_or_backfills_cost_files(tmp_path, monkeypatch):
    repo, scenes = tmp_path / "repo", tmp_path / "scenes"
    monkeypatch.setattr(scenario, "ROOT", repo)
    monkeypatch.setattr(scenario, "SCEN_DIR", scenes)
    seed_source(repo)
    existing = scenes / "已有场景"
    seed_source(existing, OPTIONAL[:1])
    (existing / "config" / OPTIONAL[0]).write_bytes(b"user-owned: 999\n")
    before = snapshot(existing)

    with pytest.raises(FileExistsError):
        scenario.create_scenario("已有场景")

    assert snapshot(existing) == before


def test_copy_tree_rejects_existing_destination_even_after_precheck(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    seed_source(source)
    target.mkdir()
    (target / "sentinel").write_bytes(b"another creator owns this destination")
    before = snapshot(target)

    with pytest.raises(FileExistsError):
        scenario._copy_tree(source, target)

    assert snapshot(target) == before


def test_ensure_default_does_not_backfill_existing_scene(tmp_path, monkeypatch):
    repo, scenes = tmp_path / "repo", tmp_path / "scenes"
    monkeypatch.setattr(scenario, "ROOT", repo)
    monkeypatch.setattr(scenario, "SCEN_DIR", scenes)
    seed_source(repo)
    existing = scenes / scenario.DEFAULT
    seed_source(existing, ())
    before = snapshot(existing)

    assert scenario.ensure_default() == existing
    assert snapshot(existing) == before
