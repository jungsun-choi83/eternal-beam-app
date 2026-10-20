from backend.services import rss_trace


def test_max_rss_falls_back_when_resource_is_unavailable(monkeypatch):
    monkeypatch.setattr(rss_trace, "_resource", None)
    monkeypatch.setattr(rss_trace, "read_rss_mb", lambda: 12.5)

    assert rss_trace.read_maxrss_mb() == 12.5


def test_max_rss_keeps_resource_units_on_linux(monkeypatch):
    class FakeResource:
        RUSAGE_SELF = object()

        @staticmethod
        def getrusage(_who):
            return type("Usage", (), {"ru_maxrss": 2048})()

    monkeypatch.setattr(rss_trace, "_resource", FakeResource)
    monkeypatch.setattr(rss_trace.sys, "platform", "linux")

    assert rss_trace.read_maxrss_mb() == 2.0
