from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from mkdocs.exceptions import PluginError

from mkdocs_h5p.plugin import H5PPlugin


def make_h5p(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("h5p.json", json.dumps({"title": "Example"}))
        archive.writestr("content/content.json", "{}")


def make_page(markdown_file: Path) -> SimpleNamespace:
    return SimpleNamespace(
        url="guide/index.html",
        file=SimpleNamespace(abs_src_path=str(markdown_file)),
    )


def configured_plugin(tmp_path: Path) -> H5PPlugin:
    plugin = H5PPlugin()
    plugin.load_config({})
    plugin.on_config({"docs_dir": str(tmp_path / "docs"), "site_dir": str(tmp_path / "site")})
    return plugin


def test_replaces_image_h5p_reference(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    page_dir = docs / "guide"
    page_dir.mkdir(parents=True)
    markdown_file = page_dir / "index.md"
    markdown_file.write_text("![h5p](quiz.h5p)", encoding="utf-8")
    make_h5p(page_dir / "quiz.h5p")

    plugin = configured_plugin(tmp_path)
    output = plugin.on_page_markdown(
        markdown_file.read_text(encoding="utf-8"),
        page=make_page(markdown_file),
        config={},
        files=None,
    )

    assert 'class="mkdocs-h5p"' in output
    assert "h5p-standalone@3.8.0/dist/main.bundle.js" in output
    assert (tmp_path / "site" / "assets" / "h5p").is_dir()


def test_replaces_shortcode_reference(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    markdown_file = docs / "index.md"
    markdown_file.write_text('{{ h5p("quiz.h5p") }}', encoding="utf-8")
    make_h5p(docs / "quiz.h5p")

    plugin = configured_plugin(tmp_path)
    output = plugin.on_page_markdown(
        markdown_file.read_text(encoding="utf-8"),
        page=make_page(markdown_file),
        config={},
        files=None,
    )

    assert "new window.H5PStandalone.H5P" in output
    assert "assets/h5p/quiz-" in output


def test_supports_multiple_h5p_files_on_one_page(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    markdown_file = docs / "index.md"
    markdown_file.write_text(
        "\n\n".join(
            [
                "![h5p](first.h5p)",
                '{{ h5p("second.h5p") }}',
            ]
        ),
        encoding="utf-8",
    )
    make_h5p(docs / "first.h5p")
    make_h5p(docs / "second.h5p")

    plugin = configured_plugin(tmp_path)
    output = plugin.on_page_markdown(
        markdown_file.read_text(encoding="utf-8"),
        page=make_page(markdown_file),
        config={},
        files=None,
    )

    assert output.count('class="mkdocs-h5p"') == 2
    assert output.count('"h5pJsonPath"') == 2
    assert output.count('"options":{"id":"mkdocs-h5p-') == 2
    assert "for (var index = 0; index < players.length; index += 1)" in output


def test_rejects_unsafe_archive_paths(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    markdown_file = docs / "index.md"
    archive_file = docs / "bad.h5p"
    with zipfile.ZipFile(archive_file, "w") as archive:
        archive.writestr("../escape.txt", "bad")

    plugin = configured_plugin(tmp_path)

    with pytest.raises(PluginError, match="Unsafe path"):
        plugin.on_page_markdown(
            "![h5p](bad.h5p)",
            page=make_page(markdown_file),
            config={},
            files=None,
        )
