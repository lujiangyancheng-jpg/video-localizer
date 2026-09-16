from __future__ import annotations

import zipfile

from youtube_localizer.models import ProjectPaths, SourceMetadata
from youtube_localizer.state import PipelineState
from youtube_localizer.support import create_support_bundle
from youtube_localizer.utils.files import atomic_write_json


def test_support_bundle_redacts_identifiers_credentials_and_media_text(
    tmp_path, monkeypatch
) -> None:
    from youtube_localizer import support

    project = ProjectPaths(tmp_path / "project")
    project.create()
    metadata = SourceMetadata(
        source_type="youtube",
        source_input="https://youtube.example/watch?v=private",
        source_url="https://youtube.example/watch?v=private",
        video_id="private-video",
        title="Private video title",
        description="Do not share this description",
    )
    atomic_write_json(project.metadata, metadata.model_dump(mode="json"))
    PipelineState(project.state_file, source_input=metadata.source_input)
    atomic_write_json(
        project.root / "config.resolved.json",
        {"translation": {"endpoint": "https://api.example/private", "api_key": "secret"}},
    )
    atomic_write_json(
        project.logs / "report.json",
        {
            "source": metadata.model_dump(mode="json"),
            "warnings": ["See C:\\Users\\Alice\\video.mp4"],
        },
    )
    (project.logs / "pipeline.log").write_text(
        "API key=super-secret https://youtube.example/private C:\\Users\\Alice\\video.mp4\n",
        encoding="utf-8",
    )
    (project.subtitles / "chinese.srt").write_text("Never include subtitle text", encoding="utf-8")
    monkeypatch.setattr(support, "run_doctor", lambda _directory: [])

    bundle = create_support_bundle(project)

    with zipfile.ZipFile(bundle) as archive:
        assert all("subtitles" not in name for name in archive.namelist())
        combined = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert "Private video title" not in combined
    assert "super-secret" not in combined
    assert "https://youtube.example" not in combined
    assert "C:\\Users\\Alice" not in combined
    assert "Never include subtitle text" not in combined
    assert "<redacted>" in combined
