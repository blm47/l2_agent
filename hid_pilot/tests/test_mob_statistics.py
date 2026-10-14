from hid_pilot.mob_statistics import MobStatistics
from hid_pilot.target import TargetObservation


def mob(at, hp, name="Gremlin", kind="monster", epoch=0):
    return TargetObservation(at=at, kind=kind, name=name, hp=hp, dead=hp == 0,
                             confidence=.99, epoch=epoch)


def observe(stats, observations):
    for target in observations:
        stats.observe(target, target.at)


def test_live_to_dead_counts_once_and_can_count_next_monster_with_same_name():
    stats = MobStatistics(0.)
    observe(stats, [mob(0., 1), mob(.5, .8), mob(1., 0), mob(1.5, 0), mob(2., 0), mob(2.5, 0)])
    assert stats.counts == {"Gremlin": 1}
    observe(stats, [mob(3., 1), mob(3.5, .8), mob(4., 0), mob(4.5, 0)])
    assert stats.counts == {"Gremlin": 2}


def test_already_dead_player_npc_unknown_and_target_disappearance_never_count():
    stats = MobStatistics(0.)
    observe(stats, [mob(0., 0), mob(.5, 0)])
    for index, kind in enumerate(("player", "npc", "unknown")):
        observe(stats, [mob(1. + index, 1, kind=kind), mob(1.5 + index, 0, kind=kind)])
    observe(stats, [mob(5., 1), mob(5.5, .8), TargetObservation(at=6.), mob(6.5, 0), mob(7., 0)])
    assert sum(stats.counts.values()) == 0


def test_unreadable_hp_or_long_gap_or_selection_change_breaks_continuity():
    for interruption in (TargetObservation(at=1., kind="monster", name="Gremlin", confidence=.99),
                         mob(4., 0), mob(1., 0, epoch=1)):
        stats = MobStatistics(0.)
        observe(stats, [mob(0., 1), mob(.5, .8), interruption,
                        mob(interruption.at + .5, 0, epoch=interruption.epoch)])
        assert not stats.counts


def test_different_name_dead_target_is_not_previous_monster_kill():
    stats = MobStatistics(0.)
    observe(stats, [mob(0., 1), mob(.5, .8), mob(1., 0, name="Wolf"), mob(1.5, 0, name="Wolf")])
    assert not stats.counts


def test_stale_or_repeated_frame_and_one_dead_glitch_do_not_increase_counter():
    stats = MobStatistics(0.)
    observe(stats, [mob(0., 1), mob(.5, .8), mob(1., 0), mob(1., 0), mob(1.5, .7)])
    assert not stats.counts
    stats.observe(mob(2., 0), 5.)
    observe(stats, [mob(5., 0), mob(5.5, 0)])
    assert not stats.counts


def test_report_once_a_minute_contains_names_counts_and_elapsed_time(caplog):
    stats = MobStatistics(0.)
    observe(stats, [mob(0., 1), mob(.5, .8), mob(1., 0), mob(1.5, 0)])
    observe(stats, [mob(2., 1, "Wolf"), mob(2.5, .8, "Wolf"), mob(3., 0, "Wolf"), mob(3.5, 0, "Wolf")])
    assert stats.report(59.) is None
    with caplog.at_level("INFO", logger="hid_pilot.mob_statistics"):
        summary = stats.report(60.)
    assert "всего 2" in summary and "Gremlin ×1" in summary and "Wolf ×1" in summary
    assert "время 1:00" in summary and len(caplog.records) == 1
    assert stats.report(61.) is None
    assert "последний интервал 0" in stats.report(120.)
