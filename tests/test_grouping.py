"""The exact index (leader_cluster) gives the same groups as comparing faces one by one."""
import numpy as np
import pytest

import store


def naive(E, order, threshold, folder_of=None, bonus=0.0):
    cents, sums, of, in_folder = [], [], np.full(len(E), -1), {}
    for i in order:
        e = E[i]
        fo = int(folder_of[i]) if folder_of is not None else -1
        if cents:
            sims = np.array(cents) @ e
            if bonus and fo >= 0 and in_folder.get(fo):
                sims[list(in_folder[fo])] += bonus
            j = int(np.argmax(sims))
            if sims[j] >= threshold:
                sums[j] = sums[j] + e
                cents[j] = sums[j] / (np.linalg.norm(sums[j]) or 1)
                of[i] = j
                if fo >= 0:
                    in_folder.setdefault(fo, set()).add(j)
                continue
        cents.append(e.copy())
        sums.append(e.copy())
        of[i] = len(cents) - 1
        if fo >= 0:
            in_folder.setdefault(fo, set()).add(of[i])
    return of


def data(n_people, per, dim=64, noise=0.5, seed=0):
    rnd = np.random.default_rng(seed)
    base = rnd.normal(size=(n_people, dim)).astype(np.float32)
    base /= np.linalg.norm(base, axis=1, keepdims=True)
    E = np.repeat(base, per, axis=0) + rnd.normal(size=(n_people * per, dim)).astype(np.float32) * noise / np.sqrt(dim)
    E /= np.linalg.norm(E, axis=1, keepdims=True)
    order = rnd.permutation(len(E))
    return E.astype(np.float32), order


def same_partition(a, b, order):
    """The same faces grouped together (group numbers may differ)."""
    ma, mb = {}, {}
    for i in order:
        if ma.setdefault(a[i], b[i]) != b[i] or mb.setdefault(b[i], a[i]) != a[i]:
            return False
    return True


@pytest.mark.parametrize("n_people, per", [(40, 5), (3000, 2)])     # the second passes 2048 groups: block path
def test_leader_cluster_matches_one_by_one(n_people, per):
    E, order = data(n_people, per)
    got, cents, _ = store.leader_cluster(E, order, 0.5)
    want = naive(E, order, 0.5)
    assert same_partition(got, want, order)
    assert len(cents) == len(set(want[order]))


def test_folder_bonus_matches_one_by_one():
    E, order = data(300, 4, noise=0.9, seed=3)
    folder_of = np.arange(len(E)) % 7
    got, _, _ = store.leader_cluster(E, order, 0.55, folder_of=folder_of, bonus=0.03)
    want = naive(E, order, 0.55, folder_of=folder_of, bonus=0.03)
    assert same_partition(got, want, order)


def test_leader_cluster_cancel():
    E, order = data(50, 4)
    assert store.leader_cluster(E, order, 0.5, cancelled=lambda: True) == (None, None, None)


def test_leader_groups_assigns_every_face():
    E, order = data(30, 6)
    groups = store._leader_groups(E, order, 0.5)
    assert all(groups.get(int(i), -1) >= 0 for i in order)
