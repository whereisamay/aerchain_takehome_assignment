"""The app must never read the answer key or the dataset generator."""
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def test_app_never_references_dataset_or_answer_key():
    offenders = []
    for f in APP.rglob("*.py"):
        text = f.read_text()
        for needle in ("DATASET_NOTES", "dataset/", "dataset\\", "generate_vendor_files", '"dataset"', "'dataset'"):
            if needle in text:
                offenders.append(f"{f.relative_to(APP.parent)}: {needle}")
    assert not offenders, offenders
