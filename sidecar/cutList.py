"""range math for keep/cut lists, all times in source seconds."""


def mergeRanges(ranges: list, gap: float = 0.0) -> list:
    out = []
    for start, end in sorted([r[0], r[1]] for r in ranges if r[1] > r[0]):
        if out and start <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def subtractRanges(base: list, cuts: list) -> list:
    cuts = mergeRanges(cuts)
    out = []
    for start, end in mergeRanges(base):
        cursor = start
        for cutStart, cutEnd in cuts:
            if cutEnd <= cursor or cutStart >= end:
                continue
            if cutStart > cursor:
                out.append([cursor, cutStart])
            cursor = max(cursor, cutEnd)
            if cursor >= end:
                break
        if cursor < end:
            out.append([cursor, end])
    return out


def invertRanges(ranges: list, duration: float) -> list:
    return subtractRanges([[0.0, duration]], ranges)


def padRanges(ranges: list, pad: float, duration: float) -> list:
    return mergeRanges([[max(0.0, s - pad), min(duration, e + pad)] for s, e in ranges])


def shrinkRanges(ranges: list, amount: float) -> list:
    return [[s + amount, e - amount] for s, e in ranges if e - s > 2 * amount]


def totalLength(ranges: list) -> float:
    return sum(e - s for s, e in ranges)


def buildKeepRanges(duration: float, silences: list, utterances: list | None = None,
                    dropIds: list | None = None, extraCuts: list | None = None,
                    padSec: float = 0.12, mergeGapSec: float = 0.15, minKeepSec: float = 0.2) -> list:
    """keep = speech minus dropped takes minus dead air, with breathing room around words."""
    dropSet = set(dropIds or [])

    if utterances:
        kept = [[u["start"], u["end"]] for u in utterances if u["id"] not in dropSet]
        keep = padRanges(kept, padSec, duration)
        # never let padding bleed into a dropped take
        droppedCores = [[u["start"], u["end"]] for u in utterances if u["id"] in dropSet]
        keep = subtractRanges(keep, droppedCores)
    else:
        keep = [[0.0, duration]]

    # dead air inside kept speech, minus the pad we want to leave around words
    keep = subtractRanges(keep, shrinkRanges(silences or [], padSec))

    if extraCuts:
        keep = subtractRanges(keep, extraCuts)

    keep = mergeRanges(keep, gap=mergeGapSec)
    keep = [[round(max(0.0, s), 3), round(min(duration, e), 3)] for s, e in keep if e - s >= minKeepSec]
    return keep


def dropsToMarkers(utterances: list, drops: list) -> list:
    """contiguous dropped utterances become one review marker."""
    byId = {u["id"]: u for u in utterances}
    reasonById = {d["id"]: d for d in drops if d["id"] in byId}
    markers = []
    for uid in sorted(reasonById):
        utt = byId[uid]
        drop = reasonById[uid]
        label = drop.get("reason", "cut")
        note = drop.get("note") or ""
        if markers and markers[-1]["lastId"] == uid - 1 and markers[-1]["reason"] == label:
            markers[-1]["end"] = utt["end"]
            markers[-1]["lastId"] = uid
            markers[-1]["comment"] += " / " + utt["text"]
            continue
        markers.append({
            "start": utt["start"],
            "end": utt["end"],
            "reason": label,
            "name": f"[chop] {label}",
            "comment": (note + " | " if note else "") + utt["text"],
            "lastId": uid,
        })
    for m in markers:
        m.pop("lastId", None)
    return markers
