from app.jobs.lock import chiave_lock


def test_chiave_lock_stabile_e_a_64_bit():
    k = chiave_lock("pipeline_notturna")
    assert k == chiave_lock("pipeline_notturna")
    assert -(2**63) <= k < 2**63


def test_chiavi_diverse_per_job_diversi():
    assert chiave_lock("raccolta") != chiave_lock("matching")
