"""可选启动预算共享件（T05-Q）：入口一次 monotonic 截止，到期只阻止开始下一步。
只含时钟/校验/到期判断；业务文案与未开始记账由各入口自己负责。"""
import time


class StartBudget:
    def __init__(self, entry_at: float, seconds: float):
        self.entry_at = entry_at
        self._seconds = seconds
        self.deadline = entry_at + seconds

    @classmethod
    def begin(cls, budget_seconds):
        """入口调用一次：返回 (StartBudget|None, 错误说明|None)。

        None → (None, None)，不取时钟。否则先 time.monotonic()（早于校验 import，
        import 耗时同样计入），再经 batch_scorer.budget_descriptor 同门校验：
        坏值（bool/非数/nan/inf/<=0）→ (None, "需有限正数秒")。
        """
        if budget_seconds is None:
            return None, None
        entry_at = time.monotonic()
        from src.core.benzong.batch_scorer import budget_descriptor
        desc = budget_descriptor(budget_seconds)
        if desc is None:
            return None, "需有限正数秒"
        return cls(entry_at, desc["budget_seconds"]), None

    @property
    def seconds(self) -> float:
        return self._seconds

    def expired(self) -> bool:
        """每次现读 time.monotonic()；恰等于截止视为到期。"""
        return time.monotonic() >= self.deadline
