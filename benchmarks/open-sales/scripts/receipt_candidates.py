"""Experimental point estimates for receipt incidence, using only known sales days."""


def estimate(candidate, incumbent, history, origin, target):
    if candidate not in {"recent_mean", "blended_mean", "blended_weekday"}:
        raise ValueError(f"Unknown receipt estimate: {candidate}")
    if incumbent is None:
        return None

    window = 56 if candidate == "blended_weekday" else 28
    observed = []
    for day in range(origin - window + 1, origin + 1):
        value = history.get(day)
        if type(value) is int and value >= 0:
            observed.append((day, value))
    if len(observed) < 7:
        return incumbent

    mean = sum(value for _, value in observed) / len(observed)
    if candidate == "recent_mean":
        return mean
    if candidate == "blended_weekday":
        weekday = [value for day, value in observed if (target - day) % 7 == 0]
        mean = (sum(weekday) + 7 * mean) / (len(weekday) + 7)
    return (incumbent + mean) / 2
