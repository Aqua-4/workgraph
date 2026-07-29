from fastapi.testclient import TestClient

from api.app import app


def test_goals_page_renders_goal_overview() -> None:
    client = TestClient(app)

    response = client.get("/goals")

    assert response.status_code == 200
    body = response.text.lower()
    assert "goals" in body or "goal allocation" in body
