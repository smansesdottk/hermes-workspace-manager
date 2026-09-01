from pathlib import Path


def test_dashboard_model_selection_ui():
    page = Path("templates/instance.html").read_text()
    assert 'id="open-quick-model"' in page
    assert 'id="custom-model-input"' in page
