from src.common.metrics import (
    REGISTRY,
    inc_complete_idempotent_hit,
    inc_cursor_decode_fail,
    inc_mq_publish_fail,
    observe_gcs_signed_url_latency_ms,
)


def test_counters_increment_independently() -> None:
    snap0 = REGISTRY.snapshot()
    base_mq = snap0["counters"].get("mq_publish_fail_count", 0)
    base_cursor = snap0["counters"].get("cursor_decode_fail_count", 0)
    base_idem = snap0["counters"].get("complete_idempotent_hit_count", 0)

    inc_mq_publish_fail()
    inc_cursor_decode_fail()
    inc_complete_idempotent_hit()

    snap1 = REGISTRY.snapshot()["counters"]
    assert snap1["mq_publish_fail_count"] == base_mq + 1
    assert snap1["cursor_decode_fail_count"] == base_cursor + 1
    assert snap1["complete_idempotent_hit_count"] == base_idem + 1


def test_latency_samples_recorded() -> None:
    observe_gcs_signed_url_latency_ms(12.5)
    observe_gcs_signed_url_latency_ms(7)
    latencies = REGISTRY.snapshot()["latencies_ms"]["gcs_signed_url_latency_ms"]
    assert 12.5 in latencies and 7.0 in latencies

