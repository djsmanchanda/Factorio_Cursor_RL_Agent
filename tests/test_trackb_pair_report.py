# Path: tests/test_trackb_pair_report.py | Purpose: Preserve terminal-only paired evidence, unknown predicates and treatment direction.
import pytest

from tools.trackb_pair_report import held_at_end, pair, strict_ever, summarize

pytestmark = pytest.mark.fast


def job(arm, seed, passed, status='completed'):
    return {'arm': arm, 'seed': seed, 'status': status,
            'strict_reached': {'C4': 100} if passed is True else {},
            'strict_current_at_end': {'C4': passed}}


def test_completed_known_pairs_only_and_wins_mean_treatment_wins(capsys):
    jobs = [job('a', 1, False), job('b', 1, True),
            job('a', 2, False), job('b', 2, True, 'running'),
            job('a', 3, True), job('b', 3, None),
            job('a', 4, True), job('b', 4, True)]
    result = pair('a', 'b', jobs, 'C4')
    assert (result['wins'], result['losses'], result['ties']) == (1, 0, 1)
    assert result['paired'] == 2 and result['unexercised'] == 2
    assert result['rate_left'] == .5 and result['rate_right'] == 1
    summarize('partial', jobs, ['C4'])
    assert 'ever=2/3 completed' in capsys.readouterr().out


def test_unknowns_and_no_pairs_do_not_become_negative_results():
    unknown = job('a', 1, None)
    assert strict_ever(unknown, 'C4') is None
    assert held_at_end(unknown, 'C4') is None
    old = {'status': 'completed', 'strict_current_at_end': {'C4': False}}
    assert strict_ever(old, 'C4') is None
    running = job('a', 1, True, 'running')
    assert held_at_end(running, 'C4') is None
    result = pair('a', 'b', [running], 'C4')
    assert result['paired'] == 0 and result['rate_left'] is None


def test_ever_latch_survives_trimmed_history_and_failed_final():
    row = job('a', 1, False)
    row['strict_reached'] = {'C4': 100}
    row['strict_current'] = [{'tick': 1000, 'statuses': {'C4': False}}]
    assert strict_ever(row, 'C4') is True
    assert held_at_end(row, 'C4') is False
