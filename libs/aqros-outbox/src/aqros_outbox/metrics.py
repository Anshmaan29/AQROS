from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OutboxMetrics:
    pending: int = 0
    processing: int = 0
    processed_total: int = 0
    failed_total: int = 0
    dead_letter_total: int = 0
    retry_total: int = 0
    dispatch_count: int = 0
    dispatch_latency_sum: float = 0.0

    @property
    def avg_dispatch_latency(self) -> float:
        if self.dispatch_count == 0:
            return 0.0
        return self.dispatch_latency_sum / self.dispatch_count

    def record_dispatch(self, latency: float) -> None:
        self.dispatch_count += 1
        self.dispatch_latency_sum += latency

    def inc_processed(self) -> None:
        self.processed_total += 1

    def inc_failed(self) -> None:
        self.failed_total += 1

    def inc_retry(self) -> None:
        self.retry_total += 1

    def inc_dead_letter(self) -> None:
        self.dead_letter_total += 1

    def snapshot(self) -> dict[str, int | float]:
        return {
            "pending": self.pending,
            "processing": self.processing,
            "processed_total": self.processed_total,
            "failed_total": self.failed_total,
            "dead_letter_total": self.dead_letter_total,
            "retry_total": self.retry_total,
            "dispatch_count": self.dispatch_count,
            "avg_dispatch_latency_ms": round(self.avg_dispatch_latency * 1000, 2),
        }


class NoopOutboxMetrics:
    def record_dispatch(self, latency: float) -> None:
        pass

    def inc_processed(self) -> None:
        pass

    def inc_failed(self) -> None:
        pass

    def inc_retry(self) -> None:
        pass

    def inc_dead_letter(self) -> None:
        pass

    def snapshot(self) -> dict[str, int | float]:
        return {}
