import json

from janteiros.world.index import World


def test_release_and_case_facts_join_the_index(tmp_path):
    pkg = tmp_path / "package"
    pkg.mkdir()
    (pkg / "facts.json").write_text(json.dumps({"news:1": {"supplier": "sup:1"}}))
    rel = tmp_path / "release-sunday"
    (rel / "data").mkdir(parents=True)
    (rel / "facts.json").write_text(json.dumps({"doc:new": {"supplier": "sup:1", "days": 30}}))
    (rel / "data" / "documents.csv").write_text(
        "id,supplier_id,subject,body\ndoc:new,sup:1,Letter,we are fully booked\n"
    )
    w = World(pkg)
    assert w.release is not None and w.facts["doc:new"]["days"] == 30
    cw = w.for_case({"parts": [], "facts": {"ctr:9": {"supplier": "sup:1", "days": 90}}})
    assert cw.known("doc:new") and cw.known("ctr:9") and cw.known("news:1") and not cw.known("doc:nope")
    assert "capacity_full" in cw.profile("sup:1")["traits"]  # release text reaches the profile


def test_release_found_next_to_a_symlinked_package(tmp_path):
    real = tmp_path / "elsewhere" / "package"
    real.mkdir(parents=True)
    (real / "facts.json").write_text("{}")
    here = tmp_path / "data"
    here.mkdir()
    (here / "package").symlink_to(real)
    (here / "release-sunday").mkdir()
    (here / "release-sunday" / "facts.json").write_text(json.dumps({"news:x": {"supplier": None}}))
    assert World(here / "package").facts.get("news:x") is not None
