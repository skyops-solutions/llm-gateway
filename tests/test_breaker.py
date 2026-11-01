from app.breaker import Breaker


def test_opens_after_threshold_and_recovers_after_cooldown():
    now = [0.0]
    b = Breaker(threshold=3, cooldown=30, clock=lambda: now[0])
    assert b.allow("p")
    assert not b.failure("p") and not b.failure("p")
    assert b.failure("p")  # third failure opens it
    assert not b.allow("p")
    now[0] = 29
    assert not b.allow("p")
    now[0] = 31
    assert b.allow("p")  # half-open: one test request
    assert b.failure("p")  # fails again, opens at once
    assert not b.allow("p")
    now[0] = 70
    assert b.allow("p")
    b.success("p")
    assert b.allow("p") and not b.is_open("p")
