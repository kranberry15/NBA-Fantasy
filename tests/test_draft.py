import time

import pandas as pd
import pytest

from src.common import DATA, load_league, norm_name
from src.draft import Draft, max_matching, my_pick_numbers, snake_slot
from src.projections import fantasy_points


def test_snake():
    assert [snake_slot(n, 10) for n in range(1, 21)] == list(range(1, 11)) + list(range(10, 0, -1))
    assert my_pick_numbers(4, 10, 13)[:4] == [4, 17, 24, 37]
    assert len(my_pick_numbers(10, 10, 13)) == 13


def test_scoring_examples():
    w = load_league("league_b")["scoring"]
    def fp(**k):
        row = {c: 0.0 for c in w}
        row.update(k)
        return float(fantasy_points(pd.DataFrame([row]), w).iloc[0])
    assert fp(PTS=2, FGM=1, FGA=1) == 3          # made 2
    assert fp(FGA=1) == -1                       # missed 2
    assert fp(PTS=3, FGM=1, FGA=1, **{"3PM": 1}) == 5   # made 3
    assert fp(FGA=1, **{"3PMI": 1}) == -1        # missed 3 (ESPN does not score 3PMI here)
    assert fp(PTS=1, FTM=1, FTA=1) == 1          # made FT
    assert fp(FTA=1, FTMI=1) == -1               # missed FT (FTA -1; ESPN does not score FTMI here)
    assert fp(DD=1) == 4 and fp(TD=1) == 7.5


def test_matching():
    assert max_matching([["PG"], ["SG"], ["SF"], ["PF"], ["C"], ["PG", "SG"], ["SF", "PF"]]) == 7
    assert max_matching([["C"], ["C"], ["C"]]) == 1


def test_leagues_not_mixed():
    a, b = load_league("league_a"), load_league("league_b")
    assert a["league_name"] != b["league_name"] and a["format"] == "categories" and b["format"] == "points"


@pytest.fixture
def draft(tmp_path):
    return Draft(load_league("league_b"), state_path=tmp_path / "s.json", fresh=True)


def test_record_find_undo_speed(draft):
    draft.set_slot(4)
    assert draft.find("jokic")
    for name in ["jokic", "gilgeous", "wembanyama"]:
        draft.record(draft.find(name)[0])
    t0 = time.time()
    rec = draft.recommend()
    assert time.time() - t0 < 2
    assert len(rec["plan"]) == 3
    assert draft.undo()["name"] == "Victor Wembanyama"


def test_center_cap(draft):
    draft.set_slot(1)
    cs = draft.board[draft.board.primary == "C"].sort_values("vorp", ascending=False).index[:3]
    for pid in cs:  # make my 3 picks centers, other picks filler
        while snake_slot(draft.current, 10) != 1:
            draft.record(draft.available().index[-1])
        draft.record(pid)
    while snake_slot(draft.current, 10) != 1:
        draft.record(draft.available().index[-1])
    plan = draft.recommend()["plan"]
    assert not (draft.board.loc[plan.id].primary == "C").any()


def test_mock_draft_runs():
    from mock_draft import run
    r = run(slot=7, seed=3, verbose=False)
    assert r["roster"] == 13 and r["centers"] <= 3 and r["slots_filled"] == 7 and r["max_ms"] < 2000
