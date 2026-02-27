from dataclasses import dataclass

@dataclass
class ConversationState:
    service: str | None = None
    day: str | None = None
    period: str | None = None   #manha tarde noite
    chosen_slot: str | None = None
