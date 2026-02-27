def get_available_slots(day: str, period: str) -> list[str]:
    #MVP : pra validar o pipe
    if day == "amanhã" and period == "tarde":
        return ["14:00", "15:00", "16:30"]
    if day == "amanhã" and period == "manhã":
        return ["08:00", "09:30", "10:30"]
    return ["11:00", "13:30", "17:00"]
