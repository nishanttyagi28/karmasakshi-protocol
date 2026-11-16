from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from karmasakshi.stores.memory import InMemoryGrantStore

_actions = st.lists(st.sampled_from(["reserve", "release", "commit"]), min_size=1, max_size=30)


@given(_actions, st.integers(min_value=1, max_value=5))
@settings(max_examples=300, deadline=None)
def test_use_count_never_exceeds_max_uses_under_any_sequential_action_order(actions, max_uses):
    store = InMemoryGrantStore()
    grant_id = "g1"
    reserved = False
    expected_uses = 0

    for i, action in enumerate(actions):
        if action == "reserve":
            ok = store.reserve(grant_id, max_uses)
            if ok:
                assert not reserved  # store must never grant a second concurrent reservation
                reserved = True
        elif action == "release":
            store.release(grant_id)
            reserved = False
        elif action == "commit" and reserved:
            store.commit(grant_id, f"idem-{i}", f"ref-{i}")
            reserved = False
            expected_uses += 1

    assert store.get_use_count(grant_id) == expected_uses
    assert store.get_use_count(grant_id) <= max_uses


@given(st.integers(min_value=1, max_value=5), st.integers(min_value=0, max_value=10))
@settings(max_examples=100, deadline=None)
def test_exactly_max_uses_reservations_succeed_then_all_further_ones_fail(max_uses, extra_attempts):
    store = InMemoryGrantStore()
    grant_id = "g1"

    successes = 0
    for i in range(max_uses + extra_attempts):
        if store.reserve(grant_id, max_uses):
            store.commit(grant_id, f"idem-{i}", f"ref-{i}")
            successes += 1

    assert successes == max_uses
    assert store.get_use_count(grant_id) == max_uses


@given(st.booleans())
@settings(max_examples=10, deadline=None)
def test_revoke_always_blocks_reservation_regardless_of_prior_state(had_prior_reserve):
    store = InMemoryGrantStore()
    grant_id = "g1"
    if had_prior_reserve:
        store.reserve(grant_id, 5)
        store.release(grant_id)
    store.revoke(grant_id)
    assert store.reserve(grant_id, 5) is False
