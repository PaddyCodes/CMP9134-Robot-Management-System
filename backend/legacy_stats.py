# backend/legacy_stats.py
from fastapi import APIRouter, HTTPException

router = APIRouter()

MISSION_RULES = {
    1: ("recon", 10),
    2: ("transport", 5),
}
MAX_SCORE = 100


def _compute_base_score(distance: float, battery: float, multiplier: float) -> float:
    if distance <= 0 or battery <= 0:
        return 0
    return (distance * multiplier) / battery


def _cap_score(score: float) -> float:
    return min(score, MAX_SCORE)


@router.post("/api/mission_stats")
def calc_stats(data: dict):
    try:
        t = data["type"]
        d = data["dist"]
        b = data["batt"]
        payload = data.get("payload_weight", 0)
    except KeyError:
        raise HTTPException(status_code=400, detail="Missing required data")

    mission = MISSION_RULES.get(t)
    if mission is None:
        return {"status": "error", "msg": "invalid mission type"}

    status, multiplier = mission
    score = _compute_base_score(d, b, multiplier)

    if status == "transport" and payload > 50 and score > 0:
        score = score - (payload * 0.1)

    score = _cap_score(score)

    query = f"INSERT INTO stats (mission, score) VALUES ('{status}', {score})"
    print(f"[DB LOG] {query}")

    return {"status": "success", "mission": status, "final_score": round(score, 2)}
